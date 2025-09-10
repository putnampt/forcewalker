import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog
import serial
import serial.tools.list_ports
import threading
import time
import pandas as pd
from PIL import Image, ImageTk
import h5py
import numpy as np
import os
import sys
import matplotlib
import json
from collections import deque
import queue
import math
import logging
import logging.handlers
from typing import Optional, Tuple, Dict, List, Any
from dataclasses import dataclass
from contextlib import contextmanager
import signal
import weakref

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt


@dataclass
class Config:
    """Application configuration"""
    SERIAL_BAUDRATE: int = 57600
    SERIAL_TIMEOUT: float = 1.0
    LIVE_PLOT_UPDATE_RATE: int = 50  # ms
    CALIBRATION_TIME: int = 5  # seconds
    TARE_TIME: int = 10  # seconds
    MAX_BUFFER_SIZE: int = 1000
    DATA_VALIDATION_THRESHOLD: float = 1000000
    MAX_TREND_CHANGE: float = 1000  # Max change between consecutive readings
    SERIAL_RECONNECT_DELAY: float = 2.0
    MAX_RECONNECT_ATTEMPTS: int = 3
    FILE_SAVE_BATCH_SIZE: int = 1000
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5


class RingBuffer:
    """Thread-safe ring buffer for efficient memory management"""

    def __init__(self, maxlen: int):
        self.maxlen = maxlen
        self.data = []
        self.index = 0
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            if len(self.data) < self.maxlen:
                self.data.append(item)
            else:
                self.data[self.index] = item
                self.index = (self.index + 1) % self.maxlen

    def get_data(self) -> List:
        with self._lock:
            if len(self.data) < self.maxlen:
                return self.data.copy()
            else:
                # Return data in correct order
                return self.data[self.index:] + self.data[:self.index]

    def clear(self):
        with self._lock:
            self.data.clear()
            self.index = 0

    def __len__(self):
        with self._lock:
            return len(self.data)


class DataValidator:
    """Enhanced data validation with context awareness"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.last_valid_values: Optional[List[float]] = None
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10

    def validate_sensor_data(self, line: str) -> Tuple[bool, Optional[List[float]], str]:
        """
        Validate sensor data line with comprehensive checks
        Returns: (is_valid, parsed_values, error_message)
        """
        try:
            # Basic format validation
            if not line or line.count(',') != 3:
                self.consecutive_errors += 1
                return False, None, f"Invalid format (expected 4 values, got {line.count(',') + 1})"

            # Parse values
            values = []
            raw_values = line.split(',')

            for i, val in enumerate(raw_values):
                val = val.strip()

                # Check for corrupted data patterns
                if '..' in val or val.count('.') > 1:
                    self.consecutive_errors += 1
                    return False, None, f"Corrupted value at position {i}: '{val}'"

                # Parse as float
                try:
                    parsed_val = float(val)
                except ValueError:
                    self.consecutive_errors += 1
                    return False, None, f"Cannot parse value at position {i}: '{val}'"

                # Range validation
                if abs(parsed_val) > Config.DATA_VALIDATION_THRESHOLD:
                    self.consecutive_errors += 1
                    return False, None, f"Value too large at position {i}: {parsed_val}"

                values.append(parsed_val)

            # Trend validation (detect unrealistic jumps)
            if self.last_valid_values and not self._validate_trend(values):
                self.consecutive_errors += 1
                return False, None, "Unrealistic data trend detected"

            # Check if we have too many consecutive errors
            if self.consecutive_errors > self.max_consecutive_errors:
                self.logger.warning(f"Too many consecutive validation errors ({self.consecutive_errors})")

            # Success - reset error counter and update last valid values
            self.consecutive_errors = 0
            self.last_valid_values = values
            return True, values, "Valid"

        except Exception as e:
            self.consecutive_errors += 1
            self.logger.error(f"Unexpected error in data validation: {e}")
            return False, None, f"Validation error: {e}"

    def _validate_trend(self, values: List[float]) -> bool:
        """Check for unrealistic jumps in sensor readings"""
        if not self.last_valid_values:
            return True

        for new_val, old_val in zip(values, self.last_valid_values):
            if abs(new_val - old_val) > Config.MAX_TREND_CHANGE:
                return False
        return True

    def reset(self):
        """Reset validation state"""
        self.last_valid_values = None
        self.consecutive_errors = 0


class ProgressDialog:
    """Thread-safe progress dialog for long operations"""

    def __init__(self, parent, title: str, max_time: float):
        self.parent = parent
        self.title = title
        self.max_time = max_time
        self.dialog = None
        self.progress = None
        self.label = None
        self.cancelled = False
        self._create_dialog()

    def _create_dialog(self):
        if self.parent:
            self.dialog = tk.Toplevel(self.parent)
            self.dialog.title(self.title)
            self.dialog.geometry("400x150")
            self.dialog.resizable(False, False)

            # Center on parent
            self.dialog.transient(self.parent)
            self.dialog.grab_set()

            # Progress bar
            self.progress = ttk.Progressbar(
                self.dialog,
                mode='determinate',
                maximum=100
            )
            self.progress.pack(pady=20, padx=20, fill=tk.X)

            # Status label
            self.label = ttk.Label(self.dialog, text="Starting...")
            self.label.pack(pady=5)

            # Cancel button
            cancel_btn = ttk.Button(
                self.dialog,
                text="Cancel",
                command=self._cancel
            )
            cancel_btn.pack(pady=10)

            self.dialog.protocol("WM_DELETE_WINDOW", self._cancel)

    def update(self, progress_percent: float, message: str):
        """Update progress dialog from any thread"""
        if self.dialog and not self.cancelled:
            def _update():
                try:
                    if self.progress:
                        self.progress['value'] = progress_percent
                    if self.label:
                        self.label.config(text=message)
                    self.dialog.update()
                except tk.TclError:
                    # Dialog was destroyed
                    pass

            if self.parent:
                self.parent.after(0, _update)

    def _cancel(self):
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled

    def close(self):
        if self.dialog:
            try:
                self.dialog.destroy()
            except tk.TclError:
                pass
            finally:
                self.dialog = None


class SafeThread(threading.Thread):
    """Enhanced thread with proper cleanup and error handling"""

    def __init__(self, target, args=(), kwargs=None, logger=None):
        super().__init__(target=target, args=args, kwargs=kwargs or {})
        self.daemon = True
        self._stop_event = threading.Event()
        self.logger = logger or logging.getLogger(__name__)
        self._exception = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.logger.error(f"Thread {self.name} failed: {e}", exc_info=True)
            self._exception = e

    def stop(self):
        self._stop_event.set()

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def join_with_timeout(self, timeout: float) -> bool:
        """Join with timeout, return True if thread finished"""
        self.join(timeout=timeout)
        return not self.is_alive()

    def get_exception(self):
        return self._exception


class SerialHandler:
    """Robust serial communication handler"""

    def __init__(self, logger: logging.Logger, data_callback=None, status_callback=None):
        self.logger = logger
        self.data_callback = data_callback
        self.status_callback = status_callback
        self.serial: Optional[serial.Serial] = None
        self.is_connected = False
        self.is_reading = False
        self._read_thread: Optional[SafeThread] = None
        self._lock = threading.RLock()
        self.reconnect_attempts = 0
        self.validator = DataValidator(logger)

    @contextmanager
    def _serial_operation(self):
        """Context manager for safe serial operations"""
        try:
            with self._lock:
                if not self.serial or not self.serial.is_open:
                    raise serial.SerialException("Serial port not open")
                yield self.serial
        except serial.SerialException as e:
            self.logger.error(f"Serial operation failed: {e}")
            self._handle_disconnect()
            raise
        except Exception as e:
            self.logger.error(f"Unexpected serial error: {e}")
            raise

    def connect(self, port: str) -> bool:
        """Connect to serial port with error handling"""
        try:
            with self._lock:
                if self.is_connected:
                    self.disconnect()

                self.logger.info(f"Attempting to connect to {port}")
                self.serial = serial.Serial(
                    port,
                    Config.SERIAL_BAUDRATE,
                    timeout=Config.SERIAL_TIMEOUT
                )

                self.is_connected = True
                self.reconnect_attempts = 0
                self.validator.reset()

                # Start reading thread
                self.is_reading = True
                self._read_thread = SafeThread(
                    target=self._read_loop,
                    logger=self.logger
                )
                self._read_thread.start()

                self.logger.info(f"Successfully connected to {port}")
                if self.status_callback:
                    self.status_callback("Connected!")
                return True

        except serial.SerialException as e:
            self.logger.error(f"Failed to connect to {port}: {e}")
            if self.status_callback:
                self.status_callback(f"Connection failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error connecting to {port}: {e}")
            if self.status_callback:
                self.status_callback(f"Connection error: {e}")
            return False

    def disconnect(self):
        """Safely disconnect from serial port"""
        try:
            with self._lock:
                self.is_reading = False

                # Stop reading thread
                if self._read_thread and self._read_thread.is_alive():
                    self._read_thread.stop()
                    if not self._read_thread.join_with_timeout(2.0):
                        self.logger.warning("Read thread did not stop gracefully")

                # Close serial port
                if self.serial and self.serial.is_open:
                    self.serial.close()
                    self.logger.info("Serial port closed")

                self.is_connected = False
                if self.status_callback:
                    self.status_callback("Disconnected")

        except Exception as e:
            self.logger.error(f"Error during disconnect: {e}")

    def _read_loop(self):
        """Main serial reading loop with robust error handling"""
        buffer = ""

        while self.is_reading and not threading.current_thread().stopped():
            try:
                with self._serial_operation() as ser:
                    if ser.in_waiting > 0:
                        data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                        buffer += data

                        # Process complete lines
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()

                            if line:
                                self._process_line(line)

                time.sleep(0.001)  # Small delay to prevent busy waiting

            except serial.SerialException:
                # Connection lost, attempt reconnect
                self._attempt_reconnect()
                break
            except UnicodeDecodeError as e:
                self.logger.warning(f"Unicode decode error: {e}")
                buffer = ""  # Clear buffer on decode error
            except Exception as e:
                self.logger.error(f"Unexpected error in read loop: {e}")
                time.sleep(0.1)  # Back off on unexpected errors

    def _process_line(self, line: str):
        """Process a complete line of data"""
        timestamp = time.time()

        # Handle special messages
        if line == "Starting...":
            if self.status_callback:
                self.status_callback("Arduino starting...")
            return
        elif line == "Finished Setup!":
            if self.status_callback:
                self.status_callback("Arduino ready!")
            return

        # Validate and parse sensor data
        is_valid, values, error_msg = self.validator.validate_sensor_data(line)

        if is_valid and values and self.data_callback:
            self.data_callback(timestamp, values)
        elif not is_valid:
            self.logger.debug(f"Invalid data: {error_msg} - Line: '{line}'")

    def _handle_disconnect(self):
        """Handle unexpected disconnection"""
        self.is_connected = False
        if self.status_callback:
            self.status_callback("Connection lost")
        self.logger.warning("Serial connection lost")

    def _attempt_reconnect(self):
        """Attempt to reconnect to serial port"""
        if self.reconnect_attempts >= Config.MAX_RECONNECT_ATTEMPTS:
            self.logger.error("Max reconnection attempts reached")
            return

        self.reconnect_attempts += 1
        self.logger.info(f"Attempting reconnection #{self.reconnect_attempts}")

        if self.status_callback:
            self.status_callback(f"Reconnecting... ({self.reconnect_attempts}/{Config.MAX_RECONNECT_ATTEMPTS})")

        time.sleep(Config.SERIAL_RECONNECT_DELAY)

        # Try to reconnect (this would need the original port info)
        # For now, just mark as disconnected
        self._handle_disconnect()


class DataProcessor:
    """Handle data processing, calibration, and storage"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.data = {'rr': [], 'rf': [], 'lr': [], 'lf': []}
        self.tare_values: Optional[List[float]] = None
        self.calibration_values: Optional[List[float]] = None
        self.is_tared = False
        self.is_calibrated = False
        self.is_recording = False
        self.recording_start: Optional[float] = None
        self._lock = threading.RLock()

        # Live data management
        self.live_data_buffers = {
            'rr': RingBuffer(Config.MAX_BUFFER_SIZE),
            'rf': RingBuffer(Config.MAX_BUFFER_SIZE),
            'lr': RingBuffer(Config.MAX_BUFFER_SIZE),
            'lf': RingBuffer(Config.MAX_BUFFER_SIZE),
            'time': RingBuffer(Config.MAX_BUFFER_SIZE)
        }
        self.live_data_queue = queue.Queue(maxsize=1000)

    def add_data_point(self, timestamp: float, raw_values: List[float]):
        """Add a new data point with processing"""
        try:
            with self._lock:
                # Apply tare and calibration
                processed_values = self._apply_processing(raw_values)

                # Store for recording if active
                if self.is_recording and self.recording_start:
                    relative_time = timestamp - self.recording_start
                    self.data['rr'].append((relative_time, processed_values[0]))
                    self.data['rf'].append((relative_time, processed_values[1]))
                    self.data['lr'].append((relative_time, processed_values[2]))
                    self.data['lf'].append((relative_time, processed_values[3]))

                # Add to live buffers
                self.live_data_buffers['time'].append(timestamp)
                for i, sensor in enumerate(['rr', 'rf', 'lr', 'lf']):
                    self.live_data_buffers[sensor].append(processed_values[i])

                # Add to live plot queue (non-blocking)
                try:
                    self.live_data_queue.put_nowait((timestamp, *processed_values))
                except queue.Full:
                    # Queue is full, drop oldest data
                    try:
                        self.live_data_queue.get_nowait()
                        self.live_data_queue.put_nowait((timestamp, *processed_values))
                    except queue.Empty:
                        pass

        except Exception as e:
            self.logger.error(f"Error processing data point: {e}")

    def _apply_processing(self, raw_values: List[float]) -> List[float]:
        """Apply tare and calibration to raw values"""
        processed = raw_values.copy()

        # Apply tare
        if self.is_tared and self.tare_values:
            for i in range(4):
                processed[i] -= self.tare_values[i]

        # Apply calibration
        if self.is_calibrated and self.calibration_values:
            for i in range(4):
                if self.calibration_values[i] != 0:
                    processed[i] /= self.calibration_values[i]

        return processed

    def start_recording(self):
        """Start data recording"""
        with self._lock:
            self.data = {'rr': [], 'rf': [], 'lr': [], 'lf': []}
            self.is_recording = True
            self.recording_start = time.time()
            self.logger.info("Recording started")

    def stop_recording(self):
        """Stop data recording"""
        with self._lock:
            self.is_recording = False
            self.logger.info(f"Recording stopped. Collected {len(self.data['rr'])} data points")

    def has_data(self) -> bool:
        """Check if we have recorded data"""
        with self._lock:
            return bool(self.data['rr'])

    def clear_live_data(self):
        """Clear live data buffers"""
        for buffer in self.live_data_buffers.values():
            buffer.clear()

        # Clear queue
        while not self.live_data_queue.empty():
            try:
                self.live_data_queue.get_nowait()
            except queue.Empty:
                break

    def set_tare(self, values: List[float]):
        """Set tare values"""
        with self._lock:
            self.tare_values = values.copy()
            self.is_tared = True
            self.logger.info(f"Tare values set: {values}")

    def set_calibration(self, values: List[float]):
        """Set calibration values"""
        with self._lock:
            self.calibration_values = values.copy()
            self.is_calibrated = True
            self.logger.info(f"Calibration values set: {values}")

    def get_live_data_queue(self) -> queue.Queue:
        """Get live data queue for plotting"""
        return self.live_data_queue

    def get_recording_data(self) -> Dict:
        """Get recorded data (thread-safe copy)"""
        with self._lock:
            return {
                'rr': self.data['rr'].copy(),
                'rf': self.data['rf'].copy(),
                'lr': self.data['lr'].copy(),
                'lf': self.data['lf'].copy()
            }


class FileManager:
    """Handle file operations with error handling"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.data_folder = self._get_data_folder_path()

    def _get_data_folder_path(self) -> str:
        """Get the path to the Data folder"""
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        data_folder = os.path.join(base_dir, 'Data')

        try:
            if not os.path.exists(data_folder):
                os.makedirs(data_folder)
                self.logger.info(f"Created data folder: {data_folder}")
        except Exception as e:
            self.logger.error(f"Failed to create data folder: {e}")
            # Fallback to current directory
            data_folder = os.getcwd()

        return data_folder

    def save_data(self, data: Dict, progress_callback=None) -> Tuple[bool, str]:
        """Save data in multiple formats with progress reporting"""
        try:
            if not any(data.values()):
                return False, "No data to save"

            base_filename = f"FW_{time.strftime('%Y-%m-%d_%H-%M-%S')}"

            # Update progress
            if progress_callback:
                progress_callback(10, "Preparing data...")

            # Create DataFrames
            df_data = {}
            sensor_names = {'rr': 'Right-Rear', 'rf': 'Right-Front',
                            'lr': 'Left-Rear', 'lf': 'Left-Front'}

            for sensor, name in sensor_names.items():
                if data[sensor]:
                    df_data[sensor] = pd.DataFrame(data[sensor], columns=['Timestamp', name])
                else:
                    df_data[sensor] = pd.DataFrame(columns=['Timestamp', name])

            success_files = []
            total_formats = 5  # H5, XLSX, XLS, CSV combined, CSV individual

            # 1. Save H5 format
            if progress_callback:
                progress_callback(20, "Saving H5 format...")

            if self._save_h5(base_filename, data):
                success_files.append("H5")

            # 2. Save XLSX format
            if progress_callback:
                progress_callback(40, "Saving XLSX format...")

            if self._save_xlsx(base_filename, df_data):
                success_files.append("XLSX")

            # 3. Save XLS format
            if progress_callback:
                progress_callback(60, "Saving XLS format...")

            if self._save_xls(base_filename, df_data):
                success_files.append("XLS")

            # 4. Save combined CSV
            if progress_callback:
                progress_callback(80, "Saving CSV formats...")

            if self._save_csv_combined(base_filename, data):
                success_files.append("CSV combined")

            # 5. Save individual CSVs
            if self._save_csv_individual(base_filename, df_data):
                success_files.append("CSV individual")

            if progress_callback:
                progress_callback(100, "Save complete!")

            if success_files:
                message = f"Data saved successfully in formats: {', '.join(success_files)}"
                self.logger.info(message)
                return True, message
            else:
                message = "Failed to save data in any format"
                self.logger.error(message)
                return False, message

        except Exception as e:
            error_msg = f"Error saving data: {e}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg

    def _save_h5(self, base_filename: str, data: Dict) -> bool:
        """Save data in H5 format"""
        try:
            filename = os.path.join(self.data_folder, f"{base_filename}.h5")
            with h5py.File(filename, 'w') as f:
                for sensor in ['rr', 'rf', 'lr', 'lf']:
                    if data[sensor]:
                        f.create_dataset(sensor, data=np.array(data[sensor]))
            self.logger.info(f"H5 data saved to {filename}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save H5: {e}")
            return False

    def _save_xlsx(self, base_filename: str, df_data: Dict) -> bool:
        """Save data in XLSX format"""
        try:
            filename = os.path.join(self.data_folder, f"{base_filename}.xlsx")
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                for sensor, df in df_data.items():
                    df.to_excel(writer, sheet_name=sensor.upper(), index=False)
            self.logger.info(f"XLSX data saved to {filename}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save XLSX: {e}")
            return False

    def _save_xls(self, base_filename: str, df_data: Dict) -> bool:
        """Save data in XLS format"""
        try:
            filename = os.path.join(self.data_folder, f"{base_filename}.xls")
            with pd.ExcelWriter(filename, engine='xlwt') as writer:
                for sensor, df in df_data.items():
                    df.to_excel(writer, sheet_name=sensor.upper(), index=False)
            self.logger.info(f"XLS data saved to {filename}")
            return True
        except ImportError:
            self.logger.warning("xlwt not available, skipping XLS format")
            return False
        except Exception as e:
            self.logger.error(f"Failed to save XLS: {e}")
            return False

    def _save_csv_combined(self, base_filename: str, data: Dict) -> bool:
        """Save combined CSV file"""
        try:
            filename = os.path.join(self.data_folder, f"{base_filename}_combined.csv")

            # Get all timestamps
            all_timestamps = set()
            for sensor_data in data.values():
                for timestamp, _ in sensor_data:
                    all_timestamps.add(timestamp)

            if not all_timestamps:
                return False

            # Create combined dataset
            sorted_timestamps = sorted(all_timestamps)
            sensor_dicts = {
                sensor: {t: v for t, v in sensor_data}
                for sensor, sensor_data in data.items()
            }

            combined_data = []
            sensor_names = {'rr': 'Right-Rear', 'rf': 'Right-Front',
                            'lr': 'Left-Rear', 'lf': 'Left-Front'}

            for timestamp in sorted_timestamps:
                row = {'Timestamp': timestamp}
                for sensor, name in sensor_names.items():
                    row[name] = sensor_dicts[sensor].get(timestamp, '')
                combined_data.append(row)

            df_combined = pd.DataFrame(combined_data)
            df_combined.to_csv(filename, index=False, float_format='%.6f')
            self.logger.info(f"Combined CSV saved to {filename}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save combined CSV: {e}")
            return False

    def _save_csv_individual(self, base_filename: str, df_data: Dict) -> bool:
        """Save individual CSV files"""
        try:
            for sensor, df in df_data.items():
                filename = os.path.join(self.data_folder, f"{base_filename}_{sensor.upper()}.csv")
                df.to_csv(filename, index=False, float_format='%.6f')
            self.logger.info("Individual CSV files saved")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save individual CSVs: {e}")
            return False

    def load_calibration(self) -> Tuple[bool, Dict]:
        """Load calibration data from file"""
        try:
            cal_file = os.path.join(self.data_folder, 'calibration.json')
            if os.path.exists(cal_file):
                with open(cal_file, 'r') as f:
                    cal_data = json.load(f)
                self.logger.info("Calibration data loaded successfully")
                return True, cal_data
            else:
                return False, {}
        except Exception as e:
            self.logger.error(f"Failed to load calibration: {e}")
            return False, {}

    def save_calibration(self, cal_data: Dict) -> bool:
        """Save calibration data to file"""
        try:
            cal_file = os.path.join(self.data_folder, 'calibration.json')
            with open(cal_file, 'w') as f:
                json.dump(cal_data, f, indent=2)
            self.logger.info("Calibration data saved successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save calibration: {e}")
            return False


class WalkerMonitorApp:
    """Main application class with improved architecture"""

    def __init__(self, root):
        self.root = root
        self.root.title("Walker Force Monitor v2.0")

        # Set up logging first
        self.logger = self._setup_logging()
        self.logger.info("Application starting...")

        # Initialize components
        self.data_processor = DataProcessor(self.logger)
        self.file_manager = FileManager(self.logger)
        self.serial_handler = SerialHandler(
            self.logger,
            data_callback=self.data_processor.add_data_point,
            status_callback=self._update_status
        )

        # UI state
        self.status_text = tk.StringVar()
        self.current_progress_dialog: Optional[ProgressDialog] = None
        self.live_window = None
        self.live_plot_active = False

        # Application state
        self.unsaved_data = False
        self.startup_complete = False

        # Set up UI
        self._setup_ui()

        # Load previous calibration
        self._load_previous_calibration()

        # Set up cleanup
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.logger.info("Application initialized successfully")

    def _setup_logging(self) -> logging.Logger:
        """Set up comprehensive logging"""
        logger = logging.getLogger('WalkerMonitor')
        logger.setLevel(logging.INFO)

        # Clear any existing handlers
        logger.handlers.clear()

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # File handler with rotation
        try:
            log_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'walker_monitor.log'
            )
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=Config.LOG_MAX_BYTES,
                backupCount=Config.LOG_BACKUP_COUNT
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to set up file logging: {e}")

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        return logger

    def _setup_ui(self):
        """Set up the user interface"""
        try:
            # Load and display splash image
            self._setup_splash_image()

            # Status display
            self.status_label = ttk.Label(self.root, textvariable=self.status_text)
            self.status_label.grid(row=1, column=0, columnspan=3, padx=5, pady=5)
            self.status_text.set("Ready to connect")

            # Serial connection controls
            self._setup_serial_controls()

            # Recording controls
            self._setup_recording_controls()

            # Calibration controls
            self._setup_calibration_controls()

            # Additional controls
            self._setup_additional_controls()

            # Initially disable most buttons
            self._disable_buttons()

        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            messagebox.showerror("UI Error", f"Failed to initialize interface: {e}")

    def _setup_splash_image(self):
        """Load and display splash image with error handling"""
        try:
            script_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            image_path = os.path.join(script_dir, 'app_data', 'splash.png')

            if os.path.exists(image_path):
                self.image = Image.open(image_path)
                self.image = self.image.resize((400, 400))
                self.photo = ImageTk.PhotoImage(self.image)

                self.image_label = ttk.Label(self.root, image=self.photo)
                self.image_label.grid(row=0, column=0, columnspan=3, padx=5, pady=5)
            else:
                # No image available, create placeholder
                self.image_label = ttk.Label(self.root, text="Walker Force Monitor",
                                             font=("Arial", 16, "bold"))
                self.image_label.grid(row=0, column=0, columnspan=3, padx=5, pady=20)

        except Exception as e:
            self.logger.warning(f"Failed to load splash image: {e}")
            # Create text placeholder instead
            self.image_label = ttk.Label(self.root, text="Walker Force Monitor",
                                         font=("Arial", 16, "bold"))
            self.image_label.grid(row=0, column=0, columnspan=3, padx=5, pady=20)

    def _setup_serial_controls(self):
        """Set up serial connection controls"""
        # Serial port selection
        ttk.Label(self.root, text="Select Serial Port:").grid(row=2, column=0, padx=5, pady=5)

        self.serial_port_combobox = ttk.Combobox(self.root, width=20, state="readonly")
        self.serial_port_combobox.grid(row=2, column=1, padx=5, pady=5)
        self._refresh_serial_ports()

        self.connect_button = ttk.Button(self.root, text="Connect", command=self._connect_serial)
        self.connect_button.grid(row=2, column=2, padx=5, pady=5)

        # Refresh ports button
        refresh_button = ttk.Button(self.root, text="Refresh", command=self._refresh_serial_ports)
        refresh_button.grid(row=2, column=3, padx=5, pady=5)

    def _setup_recording_controls(self):
        """Set up recording controls"""
        self.record_button = ttk.Button(self.root, text="Record Data",
                                        command=self._start_recording, state="disabled")
        self.record_button.grid(row=3, column=0, padx=5, pady=5)

        self.stop_button = ttk.Button(self.root, text="Stop Recording",
                                      command=self._stop_recording, state="disabled")
        self.stop_button.grid(row=3, column=1, padx=5, pady=5)

        self.save_button = ttk.Button(self.root, text="Save Data",
                                      command=self._save_data, state="disabled")
        self.save_button.grid(row=3, column=2, padx=5, pady=5)

    def _setup_calibration_controls(self):
        """Set up calibration controls"""
        self.tare_button = ttk.Button(self.root, text="Tare",
                                      command=self._tare, state="disabled")
        self.tare_button.grid(row=4, column=0, padx=5, pady=5)

        self.calibrate_button = ttk.Button(self.root, text="Calibrate",
                                           command=self._calibrate, state="disabled")
        self.calibrate_button.grid(row=4, column=1, padx=5, pady=5)

        self.cal_status_button = ttk.Button(self.root, text="Cal Status",
                                            command=self._show_calibration_status, state="disabled")
        self.cal_status_button.grid(row=4, column=2, padx=5, pady=5)

    def _setup_additional_controls(self):
        """Set up additional controls"""
        self.view_button = ttk.Button(self.root, text="View Data",
                                      command=self._view_data, state="disabled")
        self.view_button.grid(row=5, column=0, padx=5, pady=5)

        self.live_button = ttk.Button(self.root, text="Live Data",
                                      command=self._live_data, state="disabled")
        self.live_button.grid(row=5, column=1, padx=5, pady=5)

        self.reset_cal_button = ttk.Button(self.root, text="Reset Cal",
                                           command=self._reset_calibration, state="disabled")
        self.reset_cal_button.grid(row=5, column=2, padx=5, pady=5)

        # Close button (always enabled)
        self.close_button = ttk.Button(self.root, text="Close", command=self._on_closing)
        self.close_button.grid(row=6, column=2, padx=5, pady=5)

    def _refresh_serial_ports(self):
        """Refresh available serial ports"""
        try:
            ports = [port.device for port in serial.tools.list_ports.comports()]
            self.serial_port_combobox['values'] = ports
            if ports and not self.serial_port_combobox.get():
                self.serial_port_combobox.set(ports[0])
            self.logger.info(f"Found {len(ports)} serial ports")
        except Exception as e:
            self.logger.error(f"Failed to refresh serial ports: {e}")
            messagebox.showerror("Port Error", f"Failed to scan for serial ports: {e}")

    def _connect_serial(self):
        """Connect to selected serial port"""
        port = self.serial_port_combobox.get()
        if not port:
            messagebox.showwarning("No Port", "Please select a serial port")
            return

        self._update_status("Connecting...")

        # Run connection in separate thread to avoid blocking UI
        def connect_thread():
            success = self.serial_handler.connect(port)
            if success:
                # Enable buttons on successful connection
                self.root.after(0, lambda: [
                    self._enable_buttons(),
                    self._update_status("Connected! Waiting for device..."),
                    setattr(self, 'startup_complete', True)
                ])
            else:
                self.root.after(0, lambda: self._update_status("Connection failed"))

        thread = SafeThread(target=connect_thread, logger=self.logger)
        thread.start()

    def _start_recording(self):
        """Start data recording"""
        try:
            self.data_processor.start_recording()
            self.unsaved_data = True

            # Update UI
            self.record_button.config(state="disabled")
            self.stop_button.config(state="normal")
            self.save_button.config(state="disabled")
            self.tare_button.config(state="disabled")
            self.calibrate_button.config(state="disabled")

            self._update_status("Recording...")
            self.logger.info("Recording started by user")

        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            messagebox.showerror("Recording Error", f"Failed to start recording: {e}")

    def _stop_recording(self):
        """Stop data recording"""
        try:
            self.data_processor.stop_recording()

            # Update UI
            self.record_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.save_button.config(state="normal")
            self.tare_button.config(state="normal")
            self.calibrate_button.config(state="normal")
            self.view_button.config(state="normal")

            self._update_status("Recording stopped")
            self.logger.info("Recording stopped by user")

        except Exception as e:
            self.logger.error(f"Failed to stop recording: {e}")
            messagebox.showerror("Recording Error", f"Failed to stop recording: {e}")

    def _save_data(self):
        """Save recorded data with progress dialog"""
        if not self.data_processor.has_data():
            messagebox.showwarning("No Data", "No recorded data to save")
            return

        try:
            # Show progress dialog
            self.current_progress_dialog = ProgressDialog(
                self.root, "Saving Data", 10
            )

            def save_thread():
                try:
                    data = self.data_processor.get_recording_data()

                    def progress_callback(percent, message):
                        if self.current_progress_dialog:
                            self.current_progress_dialog.update(percent, message)

                    success, message = self.file_manager.save_data(data, progress_callback)

                    # Update UI from main thread
                    def update_ui():
                        if self.current_progress_dialog:
                            self.current_progress_dialog.close()
                            self.current_progress_dialog = None

                        if success:
                            self.unsaved_data = False
                            self._update_status("Data saved successfully!")
                            messagebox.showinfo("Save Complete", message)
                        else:
                            self._update_status("Save failed")
                            messagebox.showerror("Save Error", message)

                    self.root.after(0, update_ui)

                except Exception as e:
                    error_msg = f"Save operation failed: {e}"
                    self.logger.error(error_msg, exc_info=True)

                    def show_error():
                        if self.current_progress_dialog:
                            self.current_progress_dialog.close()
                            self.current_progress_dialog = None
                        messagebox.showerror("Save Error", error_msg)

                    self.root.after(0, show_error)

            thread = SafeThread(target=save_thread, logger=self.logger)
            thread.start()

        except Exception as e:
            self.logger.error(f"Failed to initiate save: {e}")
            messagebox.showerror("Save Error", f"Failed to start save operation: {e}")

    def _tare(self):
        """Perform tare operation with progress dialog"""
        if not self.serial_handler.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to device first")
            return

        try:
            # Show progress dialog
            progress_dialog = ProgressDialog(self.root, "Taring Sensors", Config.TARE_TIME)

            def tare_thread():
                try:
                    tare_values = [0.0, 0.0, 0.0, 0.0]
                    start_time = time.time()
                    sample_count = 0
                    invalid_count = 0

                    while (time.time() - start_time) < Config.TARE_TIME:
                        if progress_dialog.is_cancelled():
                            break

                        elapsed = time.time() - start_time
                        remaining = Config.TARE_TIME - elapsed
                        progress = (elapsed / Config.TARE_TIME) * 100

                        progress_dialog.update(
                            progress,
                            f"Taring: {remaining:.1f}s remaining, {sample_count} samples"
                        )

                        try:
                            with self.serial_handler._serial_operation() as ser:
                                line = ser.readline().decode('utf-8', errors='ignore').strip()
                                if line:
                                    validator = DataValidator(self.logger)
                                    is_valid, values, _ = validator.validate_sensor_data(line)

                                    if is_valid and values:
                                        for i in range(4):
                                            tare_values[i] += values[i]
                                        sample_count += 1
                                    else:
                                        invalid_count += 1
                        except Exception:
                            pass  # Continue on individual read errors

                        time.sleep(0.01)

                    def update_ui():
                        progress_dialog.close()

                        if progress_dialog.is_cancelled():
                            self._update_status("Tare cancelled")
                            return

                        if sample_count > 0:
                            # Calculate average
                            final_tare = [val / sample_count for val in tare_values]
                            self.data_processor.set_tare(final_tare)

                            # Save calibration data
                            self._save_calibration_data()

                            self._update_status(f"Tared! ({sample_count} samples)")
                            messagebox.showinfo("Tare Complete",
                                                f"Tare completed with {sample_count} samples\n"
                                                f"{invalid_count} invalid readings ignored")
                        else:
                            self._update_status("Tare failed - no valid data")
                            messagebox.showerror("Tare Failed", "No valid data collected during tare")

                    self.root.after(0, update_ui)

                except Exception as e:
                    error_msg = f"Tare operation failed: {e}"
                    self.logger.error(error_msg, exc_info=True)

                    def show_error():
                        progress_dialog.close()
                        messagebox.showerror("Tare Error", error_msg)

                    self.root.after(0, show_error)

            thread = SafeThread(target=tare_thread, logger=self.logger)
            thread.start()

        except Exception as e:
            self.logger.error(f"Failed to start tare: {e}")
            messagebox.showerror("Tare Error", f"Failed to start tare operation: {e}")

    def _calibrate(self):
        """Perform calibration with enhanced UI"""
        if not self.serial_handler.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to device first")
            return

        try:
            # Sensor selection dialog
            sensor_options = [
                ("Right-Rear (RR)", 0),
                ("Right-Front (RF)", 1),
                ("Left-Rear (LR)", 2),
                ("Left-Front (LF)", 3)
            ]

            # Create custom dialog for sensor selection
            selection_dialog = tk.Toplevel(self.root)
            selection_dialog.title("Select Sensor")
            selection_dialog.geometry("300x200")
            selection_dialog.transient(self.root)
            selection_dialog.grab_set()

            selected_sensor = tk.IntVar(value=-1)

            ttk.Label(selection_dialog, text="Select sensor to calibrate:").pack(pady=10)

            for name, index in sensor_options:
                ttk.Radiobutton(
                    selection_dialog,
                    text=name,
                    variable=selected_sensor,
                    value=index
                ).pack(anchor=tk.W, padx=20)

            def on_sensor_selected():
                if selected_sensor.get() >= 0:
                    selection_dialog.destroy()
                    self._perform_calibration(selected_sensor.get(), sensor_options[selected_sensor.get()][0])
                else:
                    messagebox.showwarning("No Selection", "Please select a sensor")

            def on_cancel():
                selection_dialog.destroy()

            button_frame = ttk.Frame(selection_dialog)
            button_frame.pack(pady=20)

            ttk.Button(button_frame, text="OK", command=on_sensor_selected).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side=tk.LEFT, padx=5)

        except Exception as e:
            self.logger.error(f"Failed to start calibration: {e}")
            messagebox.showerror("Calibration Error", f"Failed to start calibration: {e}")

    def _perform_calibration(self, sensor_index: int, sensor_name: str):
        """Perform calibration for specific sensor"""
        try:
            # Get calibration weight
            weight = simpledialog.askfloat(
                "Calibration Weight",
                f"Place a known weight on the {sensor_name} sensor.\n\nEnter the weight in grams:",
                minvalue=0.1,
                maxvalue=10000.0
            )

            if weight is None or weight <= 0:
                self._update_status("Calibration cancelled")
                return

            # Show progress dialog
            progress_dialog = ProgressDialog(
                self.root, f"Calibrating {sensor_name}", Config.CALIBRATION_TIME
            )

            def calibration_thread():
                try:
                    calibration_data = 0.0
                    start_time = time.time()
                    sample_count = 0

                    while (time.time() - start_time) < Config.CALIBRATION_TIME:
                        if progress_dialog.is_cancelled():
                            break

                        elapsed = time.time() - start_time
                        remaining = Config.CALIBRATION_TIME - elapsed
                        progress = (elapsed / Config.CALIBRATION_TIME) * 100

                        progress_dialog.update(
                            progress,
                            f"Calibrating {sensor_name}: {remaining:.1f}s remaining"
                        )

                        try:
                            with self.serial_handler._serial_operation() as ser:
                                line = ser.readline().decode('utf-8', errors='ignore').strip()
                                if line:
                                    validator = DataValidator(self.logger)
                                    is_valid, values, _ = validator.validate_sensor_data(line)

                                    if is_valid and values:
                                        sensor_value = values[sensor_index]

                                        # Apply tare if available
                                        if (self.data_processor.is_tared and
                                                self.data_processor.tare_values):
                                            sensor_value -= self.data_processor.tare_values[sensor_index]

                                        calibration_data += sensor_value
                                        sample_count += 1
                        except Exception:
                            pass  # Continue on individual read errors

                        time.sleep(0.01)

                    def update_ui():
                        progress_dialog.close()

                        if progress_dialog.is_cancelled():
                            self._update_status("Calibration cancelled")
                            return

                        if sample_count > 0:
                            avg_reading = calibration_data / sample_count

                            if avg_reading != 0:
                                # Initialize calibration values if needed
                                if not self.data_processor.calibration_values:
                                    self.data_processor.calibration_values = [1.0, 1.0, 1.0, 1.0]

                                # Calculate calibration factor
                                cal_factor = avg_reading / weight
                                cal_values = self.data_processor.calibration_values.copy()
                                cal_values[sensor_index] = cal_factor

                                self.data_processor.set_calibration(cal_values)

                                # Save calibration data
                                self._save_calibration_data()

                                self._update_status(f"{sensor_name} calibrated!")
                                messagebox.showinfo("Calibration Complete",
                                                    f"{sensor_name} calibrated successfully!\n"
                                                    f"Factor: {cal_factor:.4f} units/gram\n"
                                                    f"Samples: {sample_count}")
                            else:
                                self._update_status("Calibration failed - zero reading")
                                messagebox.showerror("Calibration Failed", "Zero reading during calibration")
                        else:
                            self._update_status("Calibration failed - no data")
                            messagebox.showerror("Calibration Failed", "No valid data collected")

                    self.root.after(0, update_ui)

                except Exception as e:
                    error_msg = f"Calibration failed: {e}"
                    self.logger.error(error_msg, exc_info=True)

                    def show_error():
                        progress_dialog.close()
                        messagebox.showerror("Calibration Error", error_msg)

                    self.root.after(0, show_error)

            thread = SafeThread(target=calibration_thread, logger=self.logger)
            thread.start()

        except Exception as e:
            self.logger.error(f"Failed to perform calibration: {e}")
            messagebox.showerror("Calibration Error", f"Calibration failed: {e}")

    def _show_calibration_status(self):
        """Show current calibration status"""
        try:
            if self.data_processor.calibration_values:
                status_text = "Calibration Status:\n\n"
                sensor_names = ["Right-Rear (RR)", "Right-Front (RF)",
                                "Left-Rear (LR)", "Left-Front (LF)"]

                for i, (name, cal_val) in enumerate(zip(sensor_names, self.data_processor.calibration_values)):
                    if cal_val != 1.0:
                        status_text += f"{name}: ✓ Calibrated ({cal_val:.4f})\n"
                    else:
                        status_text += f"{name}: ✗ Not calibrated\n"

                status_text += f"\nTare Status: {'✓ Tared' if self.data_processor.is_tared else '✗ Not tared'}"

                messagebox.showinfo("Calibration Status", status_text)
            else:
                messagebox.showinfo("Calibration Status", "No calibration data available")

        except Exception as e:
            self.logger.error(f"Failed to show calibration status: {e}")
            messagebox.showerror("Status Error", f"Failed to show status: {e}")

    def _reset_calibration(self):
        """Reset calibration values"""
        try:
            result = messagebox.askyesno(
                "Reset Calibration",
                "This will reset all calibration and tare values. Continue?"
            )

            if result:
                self.data_processor.calibration_values = [1.0, 1.0, 1.0, 1.0]
                self.data_processor.tare_values = [0.0, 0.0, 0.0, 0.0]
                self.data_processor.is_calibrated = False
                self.data_processor.is_tared = False

                # Delete calibration file
                try:
                    cal_file = os.path.join(self.file_manager.data_folder, 'calibration.json')
                    if os.path.exists(cal_file):
                        os.remove(cal_file)
                        self.logger.info("Calibration file deleted")
                except Exception as e:
                    self.logger.warning(f"Failed to delete calibration file: {e}")

                self._update_status("Calibration reset")
                messagebox.showinfo("Reset Complete", "Calibration and tare values have been reset")

        except Exception as e:
            self.logger.error(f"Failed to reset calibration: {e}")
            messagebox.showerror("Reset Error", f"Failed to reset calibration: {e}")

    def _view_data(self):
        """View recorded data in a plot"""
        if not self.data_processor.has_data():
            messagebox.showwarning("No Data", "No recorded data to view")
            return

        try:
            data = self.data_processor.get_recording_data()

            # Create plot
            plt.figure(figsize=(12, 8))

            colors = ['red', 'blue', 'green', 'orange']
            labels = ['Right-Rear', 'Right-Front', 'Left-Rear', 'Left-Front']

            for i, (sensor, label, color) in enumerate(zip(['rr', 'rf', 'lr', 'lf'], labels, colors)):
                if data[sensor]:
                    times = [point[0] for point in data[sensor]]
                    values = [point[1] for point in data[sensor]]
                    plt.plot(times, values, label=label, color=color, linewidth=2)

            plt.xlabel('Time (seconds)', fontsize=12)
            plt.ylabel('Force (grams)', fontsize=12)
            plt.title('Recorded Force Data', fontsize=14, fontweight='bold')
            plt.legend(fontsize=10)
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.show()

        except Exception as e:
            self.logger.error(f"Failed to view data: {e}")
            messagebox.showerror("View Error", f"Failed to display data: {e}")

    def _live_data(self):
        """Open live data plotting window"""
        if self.live_plot_active and self.live_window:
            # Window already open, bring to front
            try:
                self.live_window.lift()
                return
            except tk.TclError:
                # Window was destroyed
                self.live_plot_active = False
                self.live_window = None

        if not self.serial_handler.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to device first")
            return

        try:
            self.live_plot_active = True
            self.live_button.config(state="disabled")

            # Clear live data
            self.data_processor.clear_live_data()

            # Create live plotting window
            self._create_live_plot_window()

        except Exception as e:
            self.logger.error(f"Failed to start live data: {e}")
            messagebox.showerror("Live Data Error", f"Failed to start live data: {e}")

    def _create_live_plot_window(self):
        """Create live plotting window with enhanced features"""
        try:
            self.live_window = tk.Toplevel(self.root)
            self.live_window.title("Live Force Data")
            self.live_window.geometry("1000x700")

            # Main frame
            main_frame = ttk.Frame(self.live_window)
            main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            # Title
            title_label = ttk.Label(main_frame, text="Live Force Data",
                                    font=("Arial", 16, "bold"))
            title_label.pack(pady=(0, 10))

            # Current values frame with enhanced display
            values_frame = ttk.LabelFrame(main_frame, text="Current Values", padding=10)
            values_frame.pack(fill=tk.X, pady=(0, 10))

            # Create value displays
            self.live_labels = {}
            sensor_info = [
                ("rr", "Right-Rear", "#FF4444"),
                ("rf", "Right-Front", "#4444FF"),
                ("lr", "Left-Rear", "#44FF44"),
                ("lf", "Left-Front", "#FF8800")
            ]

            for i, (key, name, color) in enumerate(sensor_info):
                frame = ttk.Frame(values_frame)
                frame.grid(row=i // 2, column=i % 2, padx=20, pady=5, sticky="w")

                # Color indicator
                color_label = tk.Label(frame, text="●", fg=color, font=("Arial", 16))
                color_label.pack(side=tk.LEFT)

                # Value label
                value_label = ttk.Label(frame, text=f"{name}: 0.00 g",
                                        font=("Arial", 12, "bold"))
                value_label.pack(side=tk.LEFT, padx=(5, 0))

                self.live_labels[key] = value_label

            # Canvas for plotting
            canvas_frame = ttk.LabelFrame(main_frame, text="Real-time Plot", padding=5)
            canvas_frame.pack(fill=tk.BOTH, expand=True)

            self.plot_canvas = tk.Canvas(canvas_frame, bg='white', height=400)
            self.plot_canvas.pack(fill=tk.BOTH, expand=True)

            # Control frame
            control_frame = ttk.Frame(main_frame)
            control_frame.pack(fill=tk.X, pady=(10, 0))

            ttk.Button(control_frame, text="Clear Plot",
                       command=self._clear_live_plot).pack(side=tk.LEFT, padx=(0, 10))

            ttk.Button(control_frame, text="Export Current View",
                       command=self._export_live_view).pack(side=tk.LEFT, padx=(0, 10))

            ttk.Button(control_frame, text="Close",
                       command=self._close_live_plot).pack(side=tk.RIGHT)

            # Plot settings
            self.plot_margin = 60
            self.plot_colors = {"rr": "#FF4444", "rf": "#4444FF", "lr": "#44FF44", "lf": "#FF8800"}
            self.live_start_time = time.time()

            # Bind window close event
            self.live_window.protocol("WM_DELETE_WINDOW", self._close_live_plot)

            # Start updating
            self._update_live_plot()

        except Exception as e:
            self.logger.error(f"Failed to create live plot window: {e}")
            self.live_plot_active = False
            raise

    def _update_live_plot(self):
        """Update live plot with enhanced performance"""
        if not self.live_plot_active or not self.live_window:
            return

        try:
            # Process queued data points (limit to prevent UI lag)
            points_processed = 0
            latest_values = {}

            while not self.data_processor.live_data_queue.empty() and points_processed < 20:
                try:
                    timestamp, rr, rf, lr, lf = self.data_processor.live_data_queue.get_nowait()

                    # Store latest values for display
                    latest_values = {'rr': rr, 'rf': rf, 'lr': lr, 'lf': lf}
                    points_processed += 1

                except queue.Empty:
                    break

            # Update current value labels
            if latest_values:
                sensor_names = {"rr": "Right-Rear", "rf": "Right-Front",
                                "lr": "Left-Rear", "lf": "Left-Front"}
                for key, value in latest_values.items():
                    if key in self.live_labels:
                        self.live_labels[key].config(
                            text=f"{sensor_names[key]}: {value:7.2f} g"
                        )

            # Redraw plot if we have data
            buffer_data = self.data_processor.live_data_buffers
            if len(buffer_data['time']) > 1:
                self._draw_live_plot()

            # Schedule next update
            if self.live_window:
                self.live_window.after(Config.LIVE_PLOT_UPDATE_RATE, self._update_live_plot)

        except Exception as e:
            self.logger.error(f"Error updating live plot: {e}")
            self.live_plot_active = False

    def _draw_live_plot(self):
        """Draw the live plot with improved visualization"""
        try:
            # Clear canvas
            self.plot_canvas.delete("all")

            # Get canvas dimensions
            canvas_width = self.plot_canvas.winfo_width()
            canvas_height = self.plot_canvas.winfo_height()

            if canvas_width <= 1 or canvas_height <= 1:
                return

            # Calculate plot area
            plot_width = canvas_width - 2 * self.plot_margin
            plot_height = canvas_height - 2 * self.plot_margin

            if plot_width <= 0 or plot_height <= 0:
                return

            # Get data from buffers
            buffer_data = self.data_processor.live_data_buffers
            time_data = buffer_data['time'].get_data()

            if len(time_data) < 2:
                return

            # Calculate time range for x-axis (show last 30 seconds)
            current_time = time.time()
            time_window = 30.0  # seconds
            time_min = current_time - time_window
            time_max = current_time

            # Filter data to time window and calculate force range
            filtered_data = {}
            all_forces = []

            for sensor in ['rr', 'rf', 'lr', 'lf']:
                sensor_data = buffer_data[sensor].get_data()
                filtered_points = []

                for t, f in zip(time_data, sensor_data):
                    if t >= time_min:
                        filtered_points.append((t, f))
                        all_forces.append(f)

                filtered_data[sensor] = filtered_points

            if not all_forces:
                return

            # Calculate force range with some padding
            force_min, force_max = min(all_forces), max(all_forces)
            force_range = max(force_max - force_min, 10)  # Minimum range of 10 units
            force_padding = force_range * 0.1
            force_min -= force_padding
            force_max += force_padding
            force_range = force_max - force_min

            # Draw grid and axes
            self._draw_plot_grid(canvas_width, canvas_height, plot_width, plot_height,
                                 time_min, time_max, force_min, force_max)

            # Draw data lines
            for sensor in ['rr', 'rf', 'lr', 'lf']:
                points = filtered_data[sensor]
                if len(points) >= 2:
                    self._draw_sensor_line(points, time_min, time_max, force_min, force_range,
                                           plot_width, plot_height, self.plot_colors[sensor])

            # Draw legend
            self._draw_plot_legend(canvas_width)

        except Exception as e:
            self.logger.error(f"Error drawing live plot: {e}")

    def _draw_plot_grid(self, canvas_width, canvas_height, plot_width, plot_height,
                        time_min, time_max, force_min, force_max):
        """Draw plot grid and axes"""
        # Draw axes
        self.plot_canvas.create_line(
            self.plot_margin, canvas_height - self.plot_margin,
                              canvas_width - self.plot_margin, canvas_height - self.plot_margin,
            fill="black", width=2
        )

        self.plot_canvas.create_line(
            self.plot_margin, self.plot_margin,
            self.plot_margin, canvas_height - self.plot_margin,
            fill="black", width=2
        )

        # Draw grid lines and labels
        # Y-axis (force)
        for i in range(6):
            y_val = force_min + (force_max - force_min) * i / 5
            y_pos = canvas_height - self.plot_margin - (plot_height * i / 5)

            # Grid line
            self.plot_canvas.create_line(
                self.plot_margin, y_pos,
                canvas_width - self.plot_margin, y_pos,
                fill="lightgray", width=1, dash=(2, 2)
            )

            # Label
            self.plot_canvas.create_text(
                self.plot_margin - 5, y_pos,
                text=f"{y_val:.1f}", anchor="e", font=("Arial", 9)
            )

        # X-axis (time) - show relative seconds
        time_range = time_max - time_min
        for i in range(6):
            relative_time = -time_range + (time_range * i / 5)
            x_pos = self.plot_margin + (plot_width * i / 5)

            # Grid line
            self.plot_canvas.create_line(
                x_pos, self.plot_margin,
                x_pos, canvas_height - self.plot_margin,
                fill="lightgray", width=1, dash=(2, 2)
            )

            # Label
            self.plot_canvas.create_text(
                x_pos, canvas_height - self.plot_margin + 15,
                text=f"{relative_time:.0f}s", anchor="n", font=("Arial", 9)
            )

        # Axis labels
        self.plot_canvas.create_text(
            canvas_width // 2, canvas_height - 20,
            text="Time (seconds ago)", anchor="n", font=("Arial", 11, "bold")
        )

        self.plot_canvas.create_text(
            15, canvas_height // 2, text="Force (grams)",
            anchor="center", font=("Arial", 11, "bold"), angle=90
        )

    def _draw_sensor_line(self, points, time_min, time_max, force_min, force_range,
                          plot_width, plot_height, color):
        """Draw line for a single sensor"""
        if len(points) < 2:
            return

        # Convert points to canvas coordinates
        canvas_points = []
        time_range = time_max - time_min

        for t, f in points:
            x = self.plot_margin + ((t - time_min) / time_range) * plot_width
            y = (self.plot_canvas.winfo_height() - self.plot_margin -
                 ((f - force_min) / force_range) * plot_height)
            canvas_points.extend([x, y])

        # Draw line
        if len(canvas_points) >= 4:
            self.plot_canvas.create_line(
                canvas_points, fill=color, width=2, smooth=True
            )

    def _draw_plot_legend(self, canvas_width):
        """Draw plot legend"""
        legend_x = canvas_width - 180
        legend_y = 30
        sensor_names = {"rr": "Right-Rear", "rf": "Right-Front",
                        "lr": "Left-Rear", "lf": "Left-Front"}

        # Legend background
        self.plot_canvas.create_rectangle(
            legend_x - 10, legend_y - 10,
            legend_x + 150, legend_y + len(sensor_names) * 25,
            fill="white", outline="gray", width=1
        )

        for i, (key, name) in enumerate(sensor_names.items()):
            y = legend_y + i * 20

            # Color indicator
            self.plot_canvas.create_line(
                legend_x, y + 5, legend_x + 20, y + 5,
                fill=self.plot_colors[key], width=3
            )

            # Text
            self.plot_canvas.create_text(
                legend_x + 25, y + 5, text=name, anchor="w", font=("Arial", 10)
            )

    def _clear_live_plot(self):
        """Clear live plot data"""
        try:
            self.data_processor.clear_live_data()
            self.live_start_time = time.time()

            if self.plot_canvas:
                self.plot_canvas.delete("all")

            # Reset value displays
            for label in self.live_labels.values():
                original_text = label.cget("text")
                sensor_name = original_text.split(":")[0]
                label.config(text=f"{sensor_name}: 0.00 g")

        except Exception as e:
            self.logger.error(f"Failed to clear live plot: {e}")

    def _export_live_view(self):
        """Export current live view data"""
        try:
            if not self.data_processor.live_data_buffers['time']:
                messagebox.showinfo("No Data", "No live data to export")
                return

            # Get current buffer data
            buffer_data = self.data_processor.live_data_buffers
            time_data = buffer_data['time'].get_data()

            if not time_data:
                messagebox.showinfo("No Data", "No live data to export")
                return

            # Create filename
            filename = f"live_data_{time.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
            filepath = os.path.join(self.file_manager.data_folder, filename)

            # Prepare data for export
            export_data = []
            for i, t in enumerate(time_data):
                row = {'Timestamp': t}
                for sensor in ['rr', 'rf', 'lr', 'lf']:
                    sensor_data = buffer_data[sensor].get_data()
                    if i < len(sensor_data):
                        row[sensor.upper()] = sensor_data[i]
                    else:
                        row[sensor.upper()] = ''
                export_data.append(row)

            # Save to CSV
            df = pd.DataFrame(export_data)
            df.to_csv(filepath, index=False, float_format='%.6f')

            messagebox.showinfo("Export Complete", f"Live data exported to:\n{filepath}")
            self.logger.info(f"Live data exported to {filepath}")

        except Exception as e:
            self.logger.error(f"Failed to export live view: {e}")
            messagebox.showerror("Export Error", f"Failed to export data: {e}")

    def _close_live_plot(self):
        """Close live plot window"""
        try:
            self.live_plot_active = False
            self.live_button.config(state="normal")

            if self.live_window:
                self.live_window.destroy()
                self.live_window = None

        except Exception as e:
            self.logger.error(f"Error closing live plot: {e}")

    def _load_previous_calibration(self):
        """Load previous calibration data"""
        try:
            success, cal_data = self.file_manager.load_calibration()

            if success and cal_data:
                self.data_processor.calibration_values = cal_data.get(
                    'calibration_values', [1.0, 1.0, 1.0, 1.0]
                )
                self.data_processor.tare_values = cal_data.get(
                    'tare_values', [0.0, 0.0, 0.0, 0.0]
                )
                self.data_processor.is_calibrated = cal_data.get('is_calibrated', False)
                self.data_processor.is_tared = cal_data.get('is_tared', False)

                if self.data_processor.is_calibrated or self.data_processor.is_tared:
                    self._update_status("Previous calibration loaded")
                    self.logger.info("Previous calibration data loaded successfully")

        except Exception as e:
            self.logger.error(f"Failed to load previous calibration: {e}")

    def _save_calibration_data(self):
        """Save current calibration data"""
        try:
            cal_data = {
                'calibration_values': self.data_processor.calibration_values or [1.0, 1.0, 1.0, 1.0],
                'tare_values': self.data_processor.tare_values or [0.0, 0.0, 0.0, 0.0],
                'is_calibrated': self.data_processor.is_calibrated,
                'is_tared': self.data_processor.is_tared,
                'timestamp': time.time()
            }

            self.file_manager.save_calibration(cal_data)

        except Exception as e:
            self.logger.error(f"Failed to save calibration data: {e}")

    def _update_status(self, status: str):
        """Update status display thread-safely"""

        def update():
            self.status_text.set(status)

        if threading.current_thread() == threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def _enable_buttons(self):
        """Enable buttons when connected"""
        self.record_button.config(state="normal")
        self.tare_button.config(state="normal")
        self.calibrate_button.config(state="normal")
        self.live_button.config(state="normal")
        self.cal_status_button.config(state="normal")
        self.reset_cal_button.config(state="normal")

        # Disable connection controls
        self.connect_button.config(state="disabled")
        self.serial_port_combobox.config(state="disabled")

    def _disable_buttons(self):
        """Disable buttons when not connected"""
        self.record_button.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.save_button.config(state="disabled")
        self.tare_button.config(state="disabled")
        self.calibrate_button.config(state="disabled")
        self.view_button.config(state="disabled")
        self.live_button.config(state="disabled")
        self.cal_status_button.config(state="disabled")
        self.reset_cal_button.config(state="disabled")

        # Enable connection controls
        self.connect_button.config(state="normal")
        self.serial_port_combobox.config(state="normal")

    def _on_closing(self):
        """Handle application closing with cleanup"""
        try:
            self.logger.info("Application closing...")

            # Check for unsaved data
            if self.unsaved_data:
                result = messagebox.askyesno(
                    "Unsaved Data",
                    "There is unsaved data. Do you want to save before closing?"
                )
                if result:
                    # Save data synchronously before closing
                    try:
                        data = self.data_processor.get_recording_data()
                        success, message = self.file_manager.save_data(data)
                        if not success:
                            self.logger.error(f"Failed to save data on exit: {message}")
                    except Exception as e:
                        self.logger.error(f"Error saving data on exit: {e}")

            # Close live plot window
            if self.live_plot_active:
                self._close_live_plot()

            # Close progress dialog
            if self.current_progress_dialog:
                self.current_progress_dialog.close()

            # Disconnect serial
            if self.serial_handler:
                self.serial_handler.disconnect()

            # Stop data processing
            if self.data_processor:
                self.data_processor.clear_live_data()

            self.logger.info("Application cleanup completed")

            # Destroy the window
            self.root.destroy()

        except Exception as e:
            self.logger.error(f"Error during application shutdown: {e}")
            # Force close if cleanup fails
            self.root.destroy()

    def _signal_handler(self, signum, frame):
        """Handle system signals for graceful shutdown"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self._on_closing()

    def run(self):
        """Run the application"""
        try:
            self.logger.info("Starting application main loop")
            self.root.mainloop()
        except Exception as e:
            self.logger.error(f"Application main loop failed: {e}", exc_info=True)
        finally:
            self.logger.info("Application terminated")


def main():
    """Main entry point"""
    try:
        # Create and run application
        root = tk.Tk()
        app = WalkerMonitorApp(root)
        app.run()
    except Exception as e:
        print(f"Failed to start application: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()