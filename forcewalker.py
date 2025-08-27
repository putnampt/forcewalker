import tkinter as tk
from tkinter import ttk
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
from tkinter import simpledialog
import matplotlib
import json
import tkinter.messagebox as messagebox
from collections import deque
import queue
import math

# before rebuilding, remove scikit learn and joblib and whatever else for analysis

matplotlib.use('TkAgg')  # Specify the backend before importing pyplot
import matplotlib.pyplot as plt


class WalkerMonitorApp:
    def __init__(self, root):
        self.column_headers = None
        self.recording_start = None
        self.root = root
        self.root.title("Walker Force Monitor")

        script_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        image_path = os.path.join(script_dir, 'app_data', 'splash.png')

        # Load transparent image
        self.image = Image.open(image_path)  # Change path to your image
        self.image = self.image.resize((400, 400))
        self.photo = ImageTk.PhotoImage(self.image)

        # Create a label to display the image
        self.image_label = ttk.Label(root, image=self.photo)
        self.image_label.grid(row=0, column=0, columnspan=3, padx=5, pady=5)

        # Create a StringVar to hold the status text
        self.status_text = tk.StringVar()

        # Create a label to display the status text
        self.status_label = ttk.Label(root, textvariable=self.status_text)
        self.status_label.grid(row=1, column=0, columnspan=3, padx=5, pady=5)

        # Initialize status text
        self.status_text.set("Ready to connect")  # Initial status

        # Create serial port label
        self.serial_port_label = ttk.Label(root, text="Select Serial Port:")
        self.serial_port_label.grid(row=2, column=0, padx=5, pady=5)

        # Create serial port combobox
        self.serial_port_combobox = ttk.Combobox(root, width=20, state="readonly")
        self.serial_port_combobox.grid(row=2, column=1, padx=5, pady=5)
        self.serial_port_combobox['values'] = self.list_serial_ports()

        # Create connect button
        self.connect_button = ttk.Button(root, text="Connect", command=self.connect_serial)
        self.connect_button.grid(row=2, column=2, padx=5, pady=5)

        # Create record data button
        self.record_data_button = ttk.Button(root, text="Record Data", command=self.start_recording, state="disabled")
        self.record_data_button.grid(row=3, column=0, padx=5, pady=5)

        # Create stop recording button
        self.stop_recording_button = ttk.Button(root, text="Stop Recording", command=self.stop_recording,
                                                state="disabled")
        self.stop_recording_button.grid(row=3, column=1, padx=5, pady=5)

        # Create save data button
        self.save_data_button = ttk.Button(root, text="Save Data", command=self.save_data, state="disabled")
        self.save_data_button.grid(row=3, column=2, padx=5, pady=5)

        # Create tare button
        self.tare_button = ttk.Button(root, text="Tare", command=self.tare, state="disabled")
        self.tare_button.grid(row=4, column=0, padx=5, pady=5)

        # Create calibrate button
        self.calibrate_button = ttk.Button(root, text="Calibrate", command=self.calibrate, state="disabled")
        self.calibrate_button.grid(row=4, column=1, padx=5, pady=5)

        # Create view data button
        self.view_data_button = ttk.Button(root, text="View Data", command=self.view_data, state="disabled")
        self.view_data_button.grid(row=4, column=2, padx=5, pady=5)

        # Add calibration status button
        self.cal_status_button = ttk.Button(root, text="Cal Status", command=self.show_calibration_status,
                                            state="disabled")
        self.cal_status_button.grid(row=5, column=0, padx=5, pady=5)

        # Add reset calibration button
        self.reset_cal_button = ttk.Button(root, text="Reset Cal", command=self.reset_calibration, state="disabled")
        self.reset_cal_button.grid(row=5, column=1, padx=5, pady=5)

        # Create live data button
        self.live_data_button = ttk.Button(root, text="Live Data", command=self.live_data, state="disabled")
        self.live_data_button.grid(row=5, column=2, padx=5, pady=5)

        # Create bluetooth button
        self.bluetooth_button = ttk.Button(root, text="Bluetooth", command=self.connect_bluetooth, state="disabled")
        self.bluetooth_button.grid(row=6, column=0, padx=5, pady=5)

        self.close_button = ttk.Button(root, text="Close", command=self.close_window)
        self.close_button.grid(row=6, column=2, padx=5, pady=5)

        # Initialize variables
        self.serial = None
        self.bluetooth_connected = False
        self.is_recording = False
        self.has_recording = False
        self.is_reading = False
        self.is_arduino_starting = False
        self.is_console_enabled = False
        self.finished_startup = False
        self.is_tared = False
        self.is_calibrated = False
        self.tare_values = None
        self.calibration_values = None  # This will be loaded from file if available
        self.unsaved_data = False
        self.serial_lock = threading.Lock()
        self.data = {'rr': [], 'rf': [], 'lr': [], 'lf': []}

        # Live plotting variables
        self.live_window = None
        self.live_plot_active = False
        self.live_data_queue = queue.Queue()
        self.live_data_buffers = {
            'rr': deque(maxlen=500),  # Keep last 500 points for smooth plotting
            'rf': deque(maxlen=500),
            'lr': deque(maxlen=500),
            'lf': deque(maxlen=500),
            'time': deque(maxlen=500)
        }
        self.live_start_time = None

        # Try to load previous calibration values when the app starts
        self.load_calibration_values()

        self.disable_buttons()

    def update_status(self, new_status):  # Update the status text
        self.status_text.set(new_status)

    def list_serial_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        return ports

    def disable_buttons(self):
        self.record_data_button.config(state="disabled")
        self.stop_recording_button.config(state="disabled")
        self.save_data_button.config(state="disabled")
        self.tare_button.config(state="disabled")
        self.calibrate_button.config(state="disabled")
        self.view_data_button.config(state="disabled")
        self.live_data_button.config(state="disabled")
        self.bluetooth_button.config(state="disabled")
        self.cal_status_button.config(state="disabled")
        self.reset_cal_button.config(state="disabled")

    def enable_buttons(self):
        self.record_data_button.config(state="normal")
        self.stop_recording_button.config(state="disabled")
        self.save_data_button.config(state="disabled")
        self.tare_button.config(state="normal")
        self.calibrate_button.config(state="normal")
        self.view_data_button.config(state="disabled")
        self.connect_button.config(state="disabled")
        self.serial_port_combobox.config(state="disabled")
        self.bluetooth_button.config(state="disabled")  # TODO: activate when threading is working
        self.cal_status_button.config(state="normal")
        self.reset_cal_button.config(state="normal")
        self.live_data_button.config(state="normal")

    def connect_serial(self):
        self.update_status("Connecting..")
        port = self.serial_port_combobox.get()
        try:
            self.serial = serial.Serial(port, 57600, timeout=1)
            self.is_reading = True
            self.thread = threading.Thread(target=self.read_serial)
            self.thread.start()
            self.update_status("Connected!")
        except serial.SerialException:
            self.update_status("Failed to connect to serial port.")

    def auto_tare(self):
        self.tare_values = [0, 0, 0, 0]
        self.is_tared = True

    def auto_cal(self):
        """
        Updated auto calibration - load from file if available, otherwise use defaults
        """
        # Try to load previous calibration
        if self.load_calibration_values():
            # Calibration loaded successfully
            pass
        else:
            # No previous calibration, use defaults
            self.calibration_values = [1.0, 1.0, 1.0, 1.0]
            self.tare_values = [0.0, 0.0, 0.0, 0.0]
            self.is_calibrated = False
            self.is_tared = False

    def read_serial(self):
        buffer = ""  # Buffer to accumulate partial data

        while self.is_reading:
            try:
                with self.serial_lock:
                    if self.serial:
                        # Read available data
                        if self.serial.in_waiting > 0:
                            data = self.serial.read(self.serial.in_waiting).decode('utf-8', errors='ignore')
                            buffer += data

                            # Process complete lines
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                line = line.strip()

                                if line:
                                    timestamp = time.time()

                                    if line == "Starting...":
                                        self.update_status("Starting Arduino")
                                        self.is_arduino_starting = True
                                        self.finished_startup = False
                                        self.disable_buttons()
                                        continue

                                    if line == "Finished Setup!":
                                        self.finished_startup = True
                                        self.is_arduino_starting = False
                                        self.auto_cal()
                                        self.auto_tare()
                                        self.update_status("Ready!")
                                        self.enable_buttons()
                                        continue

                                    # Parse sensor data with enhanced validation
                                    if self.parse_sensor_data(line, timestamp):
                                        # Data was valid and processed
                                        pass
                                    else:
                                        # Invalid data - already logged in parse_sensor_data
                                        pass

                        time.sleep(0.001)  # Small delay to prevent busy waiting

                    else:
                        self.update_status("Serial port is not open. Trying to reconnect...")
                        self.connect_serial()

            except serial.SerialException as e:
                print("Serial error:", e)
                if self.serial and self.serial.is_open:
                    self.serial.close()
            except UnicodeDecodeError as e:
                print(f"Unicode decode error: {e}")
                buffer = ""  # Clear buffer on decode error
            except Exception as e:
                print(f"Unexpected error in read_serial: {e}")

    def parse_sensor_data(self, line, timestamp):
        """
        Parse and validate sensor data line
        Returns True if data was valid and processed, False otherwise
        """
        try:
            # Basic format validation
            if not line or line.count(',') != 3:
                if self.finished_startup and len(line.strip()) > 0:
                    print(f"Invalid format (comma count): '{line}'")
                return False

            # Split and validate each value
            values = line.split(',')

            # Check each value is a valid float format
            parsed_values = []
            for i, val in enumerate(values):
                val = val.strip()

                # Check for obviously corrupted data
                if '..' in val or val.count('.') > 1:
                    if self.finished_startup:
                        print(f"Corrupted value detected at position {i}: '{val}' in line '{line}'")
                    return False

                # Try to parse as float
                try:
                    parsed_val = float(val)

                    # Sanity check - reject extremely large values (likely corrupted)
                    if abs(parsed_val) > 1000000:  # Adjust threshold as needed
                        if self.finished_startup:
                            print(f"Value too large at position {i}: {parsed_val} in line '{line}'")
                        return False

                    parsed_values.append(parsed_val)

                except ValueError:
                    if self.finished_startup:
                        print(f"Cannot parse value at position {i}: '{val}' in line '{line}'")
                    return False

            # If we get here, all values are valid
            rr, rf, lr, lf = parsed_values

            if self.is_console_enabled:
                print("RR:", rr, "RF:", rf, "LR:", lr, "LF:", lf)

            # Apply tare and calibration
            if self.is_tared:
                rr -= self.tare_values[0]
                rf -= self.tare_values[1]
                lr -= self.tare_values[2]
                lf -= self.tare_values[3]

                if self.is_calibrated:
                    rr /= self.calibration_values[0]
                    rf /= self.calibration_values[1]
                    lr /= self.calibration_values[2]
                    lf /= self.calibration_values[3]

            # Store data if recording
            if self.is_recording:
                timestamp -= self.recording_start
                self.data['rr'].append((timestamp, rr))
                self.data['rf'].append((timestamp, rf))
                self.data['lr'].append((timestamp, lr))
                self.data['lf'].append((timestamp, lf))

            # Add to live plot queue if live plotting is active
            if self.live_plot_active:
                try:
                    self.live_data_queue.put_nowait((timestamp, rr, rf, lr, lf))
                except queue.Full:
                    # Queue is full, skip this data point
                    pass

            return True

        except Exception as e:
            if self.finished_startup:
                print(f"Error parsing sensor data '{line}': {e}")
            return False

    def reset_data(self):
        # Resetting the lists for rr, rf, lr, and lf
        self.data['rr'] = []
        self.data['rf'] = []
        self.data['lr'] = []
        self.data['lf'] = []

    def connect_bluetooth(self):
        pass  # TODO reactive once working

    def start_recording(self):
        self.reset_data()
        self.is_recording = True
        self.record_data_button.config(state="disabled")
        self.stop_recording_button.config(state="normal")
        self.calibrate_button.config(state="disabled")
        self.tare_button.config(state="disabled")
        self.save_data_button.config(state="disabled")
        self.view_data_button.config(state="disabled")
        self.update_status("Recording")
        self.unsaved_data = True
        # Capture the starting timestamp
        self.recording_start = time.time()

    def stop_recording(self):
        self.is_recording = False
        self.has_recording = True
        self.stop_recording_button.config(state="disabled")
        self.record_data_button.config(state="normal")
        self.save_data_button.config(state="normal")
        self.calibrate_button.config(state="normal")
        self.tare_button.config(state="normal")
        self.save_data_button.config(state="normal")
        self.view_data_button.config(state="normal")
        self.update_status("Ready")

    def close_window(self):
        self.update_status("Closing down...")
        if self.unsaved_data:
            if tk.messagebox.askyesno("Unsaved Data", "There is unsaved data. Do you want to save before closing?"):
                self.save_data()

        # Close live plot window if open
        if self.live_plot_active:
            self.close_live_plot()

        if self.serial and self.serial.is_open:
            self.serial.close()

        self.root.destroy()

    def get_data_folder_path(self):
        """
        Get the path to the Data folder, works for both script and exe
        """
        # Get the directory where the script/exe is located
        if getattr(sys, 'frozen', False):
            # Running as exe
            base_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            base_dir = os.path.dirname(os.path.abspath(__file__))

        data_folder = os.path.join(base_dir, 'Data')

        # Create the Data folder if it doesn't exist
        if not os.path.exists(data_folder):
            os.makedirs(data_folder)

        return data_folder

    def save_data(self):
        self.update_status("Saving data...")
        if self.has_recording:
            if self.data['rr'] and self.data['rf'] and self.data['lr'] and self.data['lf']:
                # Use the new data folder path method
                data_folder = self.get_data_folder_path()

                hd_filename = os.path.join(data_folder, f"FW_{time.strftime('%Y-%m-%d_%H-%M-%S')}.h5")
                with h5py.File(hd_filename, 'w') as f:
                    f.create_dataset('rr', data=np.array(self.data['rr']))
                    f.create_dataset('rf', data=np.array(self.data['rf']))
                    f.create_dataset('lr', data=np.array(self.data['lr']))
                    f.create_dataset('lf', data=np.array(self.data['lf']))

                print(f"H5 Data saved to {hd_filename}")

                df_rr = pd.DataFrame(self.data['rr'], columns=['Timestamp', 'Right-Rear'])
                df_rf = pd.DataFrame(self.data['rf'], columns=['Timestamp', 'Right-Front'])
                df_lr = pd.DataFrame(self.data['lr'], columns=['Timestamp', 'Left-Rear'])
                df_lf = pd.DataFrame(self.data['lf'], columns=['Timestamp', 'Left-Front'])

                # Save to Excel
                excel_filename = os.path.join(data_folder, f"FW_{time.strftime('%Y-%m-%d_%H-%M-%S')}.xlsx")

                with pd.ExcelWriter(excel_filename) as writer:
                    df_rr.to_excel(writer, sheet_name='RR', index=False)
                    df_rf.to_excel(writer, sheet_name='RF', index=False)
                    df_lr.to_excel(writer, sheet_name='LR', index=False)
                    df_lf.to_excel(writer, sheet_name='LF', index=False)

                print(f"Excel Data saved to {excel_filename}")

                self.update_status("Data Saved!")
                self.unsaved_data = False
        else:
            self.update_status("No Recording found in memory!")

    def view_data(self):
        if self.has_recording:

            # Plotting rr, rf, lr, and lf over time
            plt.figure(figsize=(10, 6))
            plt.plot([data[0] for data in self.data['rr']], [data[1] for data in self.data['rr']], label='Right-Rear')
            plt.plot([data[0] for data in self.data['rf']], [data[1] for data in self.data['rf']], label='Right-Front')
            plt.plot([data[0] for data in self.data['lr']], [data[1] for data in self.data['lr']], label='Left-Rear')
            plt.plot([data[0] for data in self.data['lf']], [data[1] for data in self.data['lf']], label='Left-Front')
            plt.xlabel('Time (seconds)')
            plt.ylabel('Force (grams)')
            plt.title('Force Data Over Time')
            plt.legend()
            plt.show()
        else:
            self.update_status("No Recording found in memory!")

    def live_data(self):
        """Open live data plotting window using tkinter Canvas"""
        if self.live_plot_active and self.live_window and self.live_window.winfo_exists():
            # Window already open, bring to front
            self.live_window.lift()
            return

        if not self.finished_startup:
            self.update_status("Connect to device first!")
            return

        self.live_plot_active = True
        self.live_start_time = time.time()
        self.live_data_button.config(state="disabled")

        # Clear buffers
        for key in self.live_data_buffers:
            self.live_data_buffers[key].clear()

        # Create live plotting window
        self.create_live_plot_window()

    def create_live_plot_window(self):
        """Create fast live plotting window using tkinter Canvas"""
        self.live_window = tk.Toplevel(self.root)
        self.live_window.title("Live Force Data")
        self.live_window.geometry("900x700")

        # Create main frame
        main_frame = ttk.Frame(self.live_window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Title
        title_label = ttk.Label(main_frame, text="Live Force Data", font=("Arial", 14, "bold"))
        title_label.pack(pady=(0, 10))

        # Current values frame
        values_frame = ttk.Frame(main_frame)
        values_frame.pack(fill=tk.X, pady=(0, 10))

        # Current value labels
        self.live_labels = {}
        sensor_names = ["Right-Rear", "Right-Front", "Left-Rear", "Left-Front"]
        sensor_keys = ["rr", "rf", "lr", "lf"]
        colors = ["#FF4444", "#4444FF", "#44FF44", "#FF8800"]

        for i, (key, name, color) in enumerate(zip(sensor_keys, sensor_names, colors)):
            label = ttk.Label(values_frame, text=f"{name}: 0.00 g", font=("Arial", 12))
            label.grid(row=i // 2, column=i % 2, padx=20, pady=5, sticky="w")
            self.live_labels[key] = label

        # Canvas for plotting
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.plot_canvas = tk.Canvas(canvas_frame, bg='white', height=400)
        self.plot_canvas.pack(fill=tk.BOTH, expand=True)

        # Plot parameters
        self.plot_margin = 60
        self.plot_colors = {"rr": "#FF4444", "rf": "#4444FF", "lr": "#44FF44", "lf": "#FF8800"}

        # Control buttons
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(control_frame, text="Clear", command=self.clear_live_plot).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(control_frame, text="Close", command=self.close_live_plot).pack(side=tk.RIGHT)

        # Bind window close event
        self.live_window.protocol("WM_DELETE_WINDOW", self.close_live_plot)

        # Start updating
        self.update_live_plot()

    def update_live_plot(self):
        """Update the live plot with new data"""
        if not self.live_plot_active or not self.live_window or not self.live_window.winfo_exists():
            return

        try:
            # Process queued data points
            points_processed = 0
            latest_values = {}

            while not self.live_data_queue.empty() and points_processed < 20:  # Limit processing per update
                try:
                    timestamp, rr, rf, lr, lf = self.live_data_queue.get_nowait()

                    # Add to rolling buffers
                    relative_time = timestamp - self.live_start_time
                    self.live_data_buffers['time'].append(relative_time)
                    self.live_data_buffers['rr'].append(rr)
                    self.live_data_buffers['rf'].append(rf)
                    self.live_data_buffers['lr'].append(lr)
                    self.live_data_buffers['lf'].append(lf)

                    # Keep latest values for display
                    latest_values = {'rr': rr, 'rf': rf, 'lr': lr, 'lf': lf}
                    points_processed += 1

                except queue.Empty:
                    break

            # Update current value labels
            if latest_values:
                sensor_names = {"rr": "Right-Rear", "rf": "Right-Front", "lr": "Left-Rear", "lf": "Left-Front"}
                for key, value in latest_values.items():
                    self.live_labels[key].config(text=f"{sensor_names[key]}: {value:6.2f} g")

            # Redraw plot if we have data
            if len(self.live_data_buffers['time']) > 1:
                self.draw_live_plot()

            # Schedule next update
            self.live_window.after(50, self.update_live_plot)  # 20 FPS

        except Exception as e:
            print(f"Error updating live plot: {e}")
            self.live_plot_active = False

    def draw_live_plot(self):
        """Draw the live plot on canvas"""
        try:
            # Clear canvas
            self.plot_canvas.delete("all")

            # Get canvas dimensions
            canvas_width = self.plot_canvas.winfo_width()
            canvas_height = self.plot_canvas.winfo_height()

            if canvas_width <= 1 or canvas_height <= 1:
                return  # Canvas not ready

            # Calculate plot area
            plot_width = canvas_width - 2 * self.plot_margin
            plot_height = canvas_height - 2 * self.plot_margin

            if plot_width <= 0 or plot_height <= 0:
                return

            # Get data
            time_data = list(self.live_data_buffers['time'])

            if len(time_data) < 2:
                return

            # Calculate data ranges
            time_min, time_max = min(time_data), max(time_data)
            time_range = max(time_max - time_min, 1)  # Avoid division by zero

            # Find force data range
            all_forces = []
            for key in ['rr', 'rf', 'lr', 'lf']:
                all_forces.extend(list(self.live_data_buffers[key]))

            if all_forces:
                force_min, force_max = min(all_forces), max(all_forces)
                force_range = max(force_max - force_min, 1)  # Avoid division by zero
            else:
                force_min, force_max, force_range = 0, 100, 100

            # Draw axes
            # X-axis
            self.plot_canvas.create_line(
                self.plot_margin, canvas_height - self.plot_margin,
                                  canvas_width - self.plot_margin, canvas_height - self.plot_margin,
                fill="black", width=2
            )

            # Y-axis
            self.plot_canvas.create_line(
                self.plot_margin, self.plot_margin,
                self.plot_margin, canvas_height - self.plot_margin,
                fill="black", width=2
            )

            # Draw grid and labels
            # Y-axis labels (force)
            for i in range(5):
                y_val = force_min + (force_max - force_min) * i / 4
                y_pos = canvas_height - self.plot_margin - (plot_height * i / 4)

                # Grid line
                self.plot_canvas.create_line(
                    self.plot_margin, y_pos,
                    canvas_width - self.plot_margin, y_pos,
                    fill="lightgray", width=1
                )

                # Label
                self.plot_canvas.create_text(
                    self.plot_margin - 10, y_pos,
                    text=f"{y_val:.1f}", anchor="e", font=("Arial", 8)
                )

            # X-axis labels (time)
            for i in range(5):
                x_val = time_min + (time_max - time_min) * i / 4
                x_pos = self.plot_margin + (plot_width * i / 4)

                # Grid line
                self.plot_canvas.create_line(
                    x_pos, self.plot_margin,
                    x_pos, canvas_height - self.plot_margin,
                    fill="lightgray", width=1
                )

                # Label
                self.plot_canvas.create_text(
                    x_pos, canvas_height - self.plot_margin + 15,
                    text=f"{x_val:.1f}s", anchor="n", font=("Arial", 8)
                )

            # Draw data lines
            for sensor_key in ['rr', 'rf', 'lr', 'lf']:
                force_data = list(self.live_data_buffers[sensor_key])

                if len(force_data) < 2:
                    continue

                # Convert data to screen coordinates
                points = []
                for i, (t, f) in enumerate(zip(time_data, force_data)):
                    x = self.plot_margin + ((t - time_min) / time_range) * plot_width
                    y = canvas_height - self.plot_margin - ((f - force_min) / force_range) * plot_height
                    points.extend([x, y])

                # Draw line
                if len(points) >= 4:  # Need at least 2 points
                    self.plot_canvas.create_line(
                        points, fill=self.plot_colors[sensor_key], width=2, smooth=True
                    )

            # Draw legend
            legend_x = canvas_width - 150
            legend_y = 20
            sensor_names = {"rr": "Right-Rear", "rf": "Right-Front", "lr": "Left-Rear", "lf": "Left-Front"}

            for i, (key, name) in enumerate(sensor_names.items()):
                y = legend_y + i * 20
                # Color box
                self.plot_canvas.create_rectangle(
                    legend_x, y, legend_x + 15, y + 10,
                    fill=self.plot_colors[key], outline=self.plot_colors[key]
                )
                # Text
                self.plot_canvas.create_text(
                    legend_x + 20, y + 5, text=name, anchor="w", font=("Arial", 9)
                )

            # Labels
            self.plot_canvas.create_text(
                canvas_width // 2, canvas_height - 20,
                text="Time (seconds)", anchor="n", font=("Arial", 10, "bold")
            )

            self.plot_canvas.create_text(
                20, canvas_height // 2, text="Force (grams)",
                anchor="center", font=("Arial", 10, "bold"), angle=90
            )

        except Exception as e:
            print(f"Error drawing live plot: {e}")

    def clear_live_plot(self):
        """Clear the live plot data"""
        for key in self.live_data_buffers:
            self.live_data_buffers[key].clear()
        self.live_start_time = time.time()

        # Clear the canvas
        if self.plot_canvas:
            self.plot_canvas.delete("all")

    def close_live_plot(self):
        """Close the live plot window"""
        self.live_plot_active = False
        self.live_data_button.config(state="normal")

        # Clear the queue
        while not self.live_data_queue.empty():
            try:
                self.live_data_queue.get_nowait()
            except queue.Empty:
                break

        if self.live_window:
            self.live_window.destroy()
            self.live_window = None

    def tare(self):
        self.update_status("Zeroing")
        try:
            with self.serial_lock:
                self.tare_values = [0, 0, 0, 0]
                start_time = time.time()
                nsamples = 0
                last_status_update = start_time
                invalid_count = 0

                while time.time() - start_time < 10:
                    current_time = time.time()
                    elapsed = current_time - start_time
                    remaining = 10 - elapsed

                    # Update status every 0.5 seconds
                    if current_time - last_status_update >= 0.5:
                        self.update_status(
                            f"Zeroing: {remaining:.1f}s remaining, {nsamples} samples ({invalid_count} invalid)")
                        last_status_update = current_time

                    if self.serial and self.serial.is_open:
                        line = self.serial.readline().decode().strip()
                        if line:
                            if self.parse_sensor_data_for_calibration(line):
                                # Valid data - add to tare
                                values = line.split(',')
                                rr, rf, lr, lf = map(float, values)
                                self.tare_values[0] += rr
                                self.tare_values[1] += rf
                                self.tare_values[2] += lr
                                self.tare_values[3] += lf
                                nsamples += 1
                            else:
                                invalid_count += 1
                    else:
                        self.update_status("Serial port is not open.")
                        return

                if nsamples > 0:
                    # Calculate average values
                    self.tare_values = [val / nsamples for val in self.tare_values]
                    print(f"Tare values: {self.tare_values} (from {nsamples} samples, {invalid_count} invalid)")
                    self.update_status("Tared!")
                    self.is_tared = True

                    # Save the updated tare values
                    self.save_calibration_values()
                else:
                    self.update_status("No valid data collected for tare")

        except serial.SerialException as e:
            print("Serial error:", e)

    def parse_sensor_data_for_calibration(self, line):
        """
        Simplified validation for calibration/tare operations
        Returns True if data is valid for calibration use
        """
        try:
            if not line or line.count(',') != 3:
                return False

            values = line.split(',')

            # Quick validation
            for val in values:
                val = val.strip()
                if '..' in val or val.count('.') > 1:
                    return False
                try:
                    parsed_val = float(val)
                    if abs(parsed_val) > 1000000:  # Sanity check
                        return False
                except ValueError:
                    return False

            return True

        except Exception:
            return False

    def calibrate(self):
        """
        Calibrate individual sensors one at a time with improved status updates
        """
        try:
            self.update_status("Select sensor to calibrate...")

            # Sensor options for user selection
            sensor_options = {
                "0": ("Right-Rear (RR)", 0),
                "1": ("Right-Front (RF)", 1),
                "2": ("Left-Rear (LR)", 2),
                "3": ("Left-Front (LF)", 3)
            }

            # Ask user to select which sensor to calibrate
            selection_text = "Select sensor to calibrate:\n"
            for key, (name, _) in sensor_options.items():
                selection_text += f"{key} = {name}\n"
            selection_text += "\nEnter 0-3:"

            selected_sensor = simpledialog.askstring(
                "Sensor Selection",
                selection_text
            )

            if selected_sensor is None:  # User cancelled
                self.update_status("Calibration cancelled")
                return

            if selected_sensor not in sensor_options:
                self.update_status("Invalid sensor selection")
                return

            sensor_name, sensor_index = sensor_options[selected_sensor]

            # Ask for calibration weight for the selected sensor
            calibration_weight = simpledialog.askfloat(
                "Calibration Weight",
                f"Place a known weight on the {sensor_name} sensor.\n\nEnter the weight in grams:"
            )

            if calibration_weight is None or calibration_weight <= 0:
                self.update_status("Invalid calibration weight")
                return

            # Initialize calibration_values array if not already done
            if self.calibration_values is None:
                self.calibration_values = [1.0, 1.0, 1.0, 1.0]

            # Start calibration process
            self.update_status(f"Starting calibration for {sensor_name}...")

            with self.serial_lock:
                calibration_data = 0
                start_time = time.time()
                nsamples = 0
                last_status_update = start_time
                invalid_count = 0

                # Collect data for 5 seconds with status updates
                while time.time() - start_time < 5:
                    current_time = time.time()
                    elapsed = current_time - start_time
                    remaining = 5 - elapsed

                    # Update status every 0.5 seconds
                    if current_time - last_status_update >= 0.5:
                        self.update_status(f"Calibrating {sensor_name}: {remaining:.1f}s remaining, {nsamples} samples")
                        last_status_update = current_time

                    if self.serial and self.serial.is_open:
                        line = self.serial.readline().decode().strip()
                        if line:
                            if self.parse_sensor_data_for_calibration(line):
                                values = line.split(',')
                                rr, rf, lr, lf = map(float, values)
                                sensor_values = [rr, rf, lr, lf]

                                # Get the raw value for the selected sensor
                                sensor_value = sensor_values[sensor_index]

                                # Apply tare offset if available
                                if self.is_tared and self.tare_values:
                                    sensor_value -= self.tare_values[sensor_index]

                                calibration_data += sensor_value
                                nsamples += 1
                            else:
                                invalid_count += 1

                    else:
                        self.update_status("Serial port is not open.")
                        return

                if nsamples > 0:
                    # Calculate average reading for the selected sensor
                    avg_reading = calibration_data / nsamples

                    self.update_status(f"Processing {sensor_name} calibration...")

                    # Calculate calibration factor (raw_units_per_gram)
                    if avg_reading != 0:
                        self.calibration_values[sensor_index] = avg_reading / calibration_weight

                        print(f"Calibration complete for {sensor_name}:")
                        print(f"  Average reading: {avg_reading:.2f}")
                        print(f"  Calibration weight: {calibration_weight}g")
                        print(f"  Calibration factor: {self.calibration_values[sensor_index]:.4f} units/gram")
                        print(f"  Samples collected: {nsamples}")

                        self.is_calibrated = True

                        # Save calibration values
                        if self.save_calibration_values():
                            self.update_status(
                                f"{sensor_name} calibrated & saved! Factor: {self.calibration_values[sensor_index]:.4f}")
                        else:
                            self.update_status(
                                f"{sensor_name} calibrated! (Save failed) Factor: {self.calibration_values[sensor_index]:.4f}")

                        # Show current calibration status
                        self.show_calibration_status()
                    else:
                        self.update_status("Error: Zero reading during calibration")
                else:
                    self.update_status("No valid data collected during calibration")

        except serial.SerialException as e:
            print("Serial error:", e)
            self.update_status("Calibration failed - serial error")

    def show_calibration_status(self):
        """
        Display which sensors have been calibrated
        """
        if self.calibration_values:
            status_text = "Calibration Status:\n"
            sensor_names = ["Right-Rear (RR)", "Right-Front (RF)", "Left-Rear (LR)", "Left-Front (LF)"]

            for i, (name, cal_val) in enumerate(zip(sensor_names, self.calibration_values)):
                if cal_val != 1.0:  # Sensor has been calibrated (not default value)
                    status_text += f"{name}: ✓ ({cal_val:.4f})\n"
                else:
                    status_text += f"{name}: Not calibrated\n"

            messagebox.showinfo("Calibration Status", status_text)

    def reset_calibration(self):
        """
        Reset calibration values to defaults and delete saved file
        """
        self.calibration_values = [1.0, 1.0, 1.0, 1.0]
        self.is_calibrated = False

        # Try to delete the calibration file
        try:
            cal_file_path = self.get_calibration_file_path()
            if os.path.exists(cal_file_path):
                os.remove(cal_file_path)
                print(f"Calibration file deleted: {cal_file_path}")
        except Exception as e:
            print(f"Error deleting calibration file: {e}")

        self.update_status("Calibration reset")

    def get_calibration_file_path(self):
        """
        Get the full path to the calibration file
        """
        data_folder = self.get_data_folder_path()
        return os.path.join(data_folder, 'calibration.json')

    def save_calibration_values(self):
        """
        Save calibration values to a JSON file
        """
        if self.calibration_values:
            cal_data = {
                'calibration_values': self.calibration_values,
                'tare_values': self.tare_values if self.tare_values else [0.0, 0.0, 0.0, 0.0],
                'timestamp': time.time(),
                'is_calibrated': self.is_calibrated,
                'is_tared': self.is_tared
            }

            try:
                cal_file_path = self.get_calibration_file_path()
                with open(cal_file_path, 'w') as f:
                    json.dump(cal_data, f, indent=2)
                print(f"Calibration saved to: {cal_file_path}")
                return True
            except Exception as e:
                print(f"Error saving calibration: {e}")
                return False
        return False

    def load_calibration_values(self):
        """
        Load calibration values from JSON file if it exists
        """
        try:
            cal_file_path = self.get_calibration_file_path()
            if os.path.exists(cal_file_path):
                with open(cal_file_path, 'r') as f:
                    cal_data = json.load(f)

                self.calibration_values = cal_data.get('calibration_values', [1.0, 1.0, 1.0, 1.0])
                self.tare_values = cal_data.get('tare_values', [0.0, 0.0, 0.0, 0.0])
                self.is_calibrated = cal_data.get('is_calibrated', False)
                self.is_tared = cal_data.get('is_tared', False)

                print(f"Calibration loaded from: {cal_file_path}")
                print(f"Calibration values: {self.calibration_values}")
                print(f"Tare values: {self.tare_values}")

                # Update status to show loaded calibration
                if self.is_calibrated:
                    self.update_status("Previous calibration loaded!")

                return True
            else:
                print("No previous calibration file found")
                return False
        except Exception as e:
            print(f"Error loading calibration: {e}")
            return False

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    root = tk.Tk()
    app = WalkerMonitorApp(root)
    app.run()