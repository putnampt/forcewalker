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

        self.close_button = ttk.Button(root, text="Close", command=self.close_window)
        self.close_button.grid(row=5, column=2, padx=5, pady=5)

        # Create view data button
        self.live_data_button = ttk.Button(root, text="Live Data", command=self.live_data, state="disabled")
        self.live_data_button.grid(row=5, column=1, padx=5, pady=5)

        # Create view data button
        self.bluetooth_button = ttk.Button(root, text="Bluetooth", command=self.connect_bluetooth, state="disabled")
        self.bluetooth_button.grid(row=5, column=0, padx=5, pady=5)

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
        self.calibration_values = None
        self.unsaved_data = False
        self.serial_lock = threading.Lock()
        self.data = {'rr': [], 'rf': [], 'lr': [], 'lf': []}
        # self.hand_data = {'lhf': [], 'lhx': [], 'lhy': [], 'rhf': [], 'rhx': [], 'rhy': []}

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

    def enable_buttons(self):
        self.record_data_button.config(state="normal")
        self.stop_recording_button.config(state="disabled")
        self.save_data_button.config(state="disabled")
        self.tare_button.config(state="normal")
        self.calibrate_button.config(state="disabled")
        self.view_data_button.config(state="disabled")
        self.connect_button.config(state="disabled")
        self.serial_port_combobox.config(state="disabled")
        self.bluetooth_button.config(state="disabled")  # TODO: activate when threading is working

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
        self.calibration_values = [(72750 / 1100), (50000 / 1100), (83000 / 1100), (48000 / 1100)]
        self.is_calibrated = True

    def read_serial(self):
        while self.is_reading:
            try:
                with self.serial_lock:
                    if self.serial:
                        line = self.serial.readline().decode().strip()
                        if line:
                            timestamp = time.time()
                            if line == "Starting...":
                                self.update_status("Starting Arduino")
                                self.is_arduino_starting = True
                                self.finished_startup = False
                                self.disable_buttons()
                                continue  # Skip parsing and wait for actual data
                            if line == "Finished Setup!":
                                self.finished_startup = True
                                self.is_arduino_starting = False
                                self.auto_cal()
                                self.auto_tare()
                                self.update_status("Ready!")
                                # Enable buttons
                                self.enable_buttons()
                            else:
                                # Parse the serial data if it's in the expected format
                                try:
                                    rr, rf, lr, lf = map(float, line.split(','))
                                    if self.is_console_enabled:
                                        print("RR:", rr, "RF:", rf, "LR:", lr, "LF:", lf)
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
                                        # print("Adjusted - RR:", rr, "RF:", rf, "LR:", lr, "LF:", lf)
                                    if self.is_recording:
                                        timestamp -= self.recording_start
                                        self.data['rr'].append((timestamp, rr))
                                        self.data['rf'].append((timestamp, rf))
                                        self.data['lr'].append((timestamp, lr))
                                        self.data['lf'].append((timestamp, lf))


                                except ValueError:
                                    if self.finished_startup:
                                        print("Received data not in expected format:", line)
                                self.is_arduino_starting = False
                    else:
                        self.update_status("Serial port is not open. Trying to reconnect...")
                        self.connect_serial()  # Attempt to reconnect
            except serial.SerialException as e:
                print("Serial error:", e)
                # Close the serial port if an error occurs
                if self.serial and self.serial.is_open:
                    self.serial.close()

    def reset_data(self):
        # Resetting the lists for rr, rf, lr, and lf
        self.data['rr'] = []
        self.data['rf'] = []
        self.data['lr'] = []
        self.data['lf'] = []

        # Reset the force data for the hand dynos
        # self.hand_data['lhf'] = []
        # self.hand_data['lhx'] = []
        # self.hand_data['lhy'] = []
        # self.hand_data['rhf'] = []
        # self.hand_data['rhx'] = []
        # self.hand_data['rhy'] = []

    def connect_bluetooth(self):
        pass # TODO reactive once working

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
        if self.serial and self.serial.is_open:
            self.serial.close()


        self.root.destroy()

    def save_data(self):
        self.update_status("Saving data...")
        if self.has_recording:
            if self.data['rr'] and self.data['rf'] and self.data['lr'] and self.data['lf']:
                # Create the 'Data' folder if it does not exist
                if not os.path.exists('Data'):
                    os.makedirs('Data')

                hd_filename = f"Data/FW_{time.strftime('%Y-%m-%d_%H-%M-%S')}.h5"
                with h5py.File(hd_filename, 'w') as f:
                    f.create_dataset('rr', data=np.array(self.data['rr']))
                    f.create_dataset('rf', data=np.array(self.data['rf']))
                    f.create_dataset('lr', data=np.array(self.data['lr']))
                    f.create_dataset('lf', data=np.array(self.data['lf']))
                    '''if self.bluetooth_connected:
                        f.create_dataset('lhf', data=np.array(self.hand_data['lhf']))
                        f.create_dataset('lhx', data=np.array(self.hand_data['lhx']))
                        f.create_dataset('lhy', data=np.array(self.hand_data['lhy']))
                        f.create_dataset('rhf', data=np.array(self.hand_data['rhf']))
                        f.create_dataset('rhx', data=np.array(self.hand_data['rhx']))
                        f.create_dataset('rhy', data=np.array(self.hand_data['rhy']))'''

                print(f"H5 Data saved to {hd_filename}")

                df_rr = pd.DataFrame(self.data['rr'], columns=['Timestamp', 'Right-Rear'])
                df_rf = pd.DataFrame(self.data['rf'], columns=['Timestamp', 'Right-Front'])
                df_lr = pd.DataFrame(self.data['lr'], columns=['Timestamp', 'Left-Rear'])
                df_lf = pd.DataFrame(self.data['lf'], columns=['Timestamp', 'Left-Front'])

                '''if self.bluetooth_connected:
                    df_lhf = pd.DataFrame(self.hand_data['lhf'], columns=['Timestamp', 'Left-Hand-Force'])
                    df_lhx = pd.DataFrame(self.hand_data['lhx'], columns=['Timestamp', 'Left-Hand-X'])
                    df_lhy = pd.DataFrame(self.hand_data['lhy'], columns=['Timestamp', 'Left-Hand-Y'])
                    df_rhf = pd.DataFrame(self.hand_data['rhf'], columns=['Timestamp', 'Right-Hand-Force'])
                    df_rhx = pd.DataFrame(self.hand_data['rhx'], columns=['Timestamp', 'Right-Hand-X'])
                    df_rhy = pd.DataFrame(self.hand_data['rhy'], columns=['Timestamp', 'Right-Hand-Y'])'''

                # Save to Excel
                excel_filename = f"Data/FW_{time.strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"

                with pd.ExcelWriter(excel_filename) as writer:
                    df_rr.to_excel(writer, sheet_name='RR', index=False)
                    df_rf.to_excel(writer, sheet_name='RF', index=False)
                    df_lr.to_excel(writer, sheet_name='LR', index=False)
                    df_lf.to_excel(writer, sheet_name='LF', index=False)

                    '''if self.bluetooth_connected:
                        df_lhf.to_excel(writer, sheet_name='LHF', index=False)
                        df_lhx.to_excel(writer, sheet_name='LHX', index=False)
                        df_lhy.to_excel(writer, sheet_name='LHY', index=False)
                        df_rhf.to_excel(writer, sheet_name='RHF', index=False)
                        df_rhx.to_excel(writer, sheet_name='RHX', index=False)
                        df_rhy.to_excel(writer, sheet_name='RHY', index=False)'''

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
        if self.is_recording:
            nsamples = 0
        else:
            nsamples = 0

    def tare(self):
        self.update_status("Zeroing")
        try:
            with self.serial_lock:

                self.tare_values = [0, 0, 0, 0]
                start_time = time.time()
                nsamples = 0
                while time.time() - start_time < 10:
                    if self.serial and self.serial.is_open:
                        line = self.serial.readline().decode().strip()
                        if line:
                            try:
                                rr, rf, lr, lf = map(float, line.split(','))
                                self.tare_values[0] += rr
                                self.tare_values[1] += rf
                                self.tare_values[2] += lr
                                self.tare_values[3] += lf
                                nsamples += 1
                            except ValueError:
                                print("Received data not in expected format:", line)
                    else:
                        self.update_status("Serial port is not open.")
                        return  # Exit function if serial port is not open or device is disconnected
                # Calculate average values
                self.tare_values = [val / nsamples for val in self.tare_values]
                print("Tare values:", self.tare_values)
                self.update_status("Tared!")
                self.is_tared = True

        except serial.SerialException as e:
            print("Serial error:", e)

    def calibrate(self):
        try:
            self.update_status("Calibrating...")
            with self.serial_lock:
                calibration_weight = simpledialog.askfloat("Calibration", "Enter calibration weight (grams):")
                calibration_data = [0, 0, 0, 0]
                start_time = time.time()
                nsamples = 0
                while time.time() - start_time < 5:
                    if self.serial and self.serial.is_open:
                        line = self.serial.readline().decode().strip()
                        if line:
                            try:
                                rr, rf, lr, lf = map(float, line.split(','))

                                if self.is_tared:
                                    rr -= self.tare_values[0]
                                    rf -= self.tare_values[1]
                                    lr -= self.tare_values[2]
                                    lf -= self.tare_values[3]

                                calibration_data[0] += rr
                                calibration_data[1] += rf
                                calibration_data[2] += lr
                                calibration_data[3] += lf
                                nsamples += 1
                            except ValueError:
                                print("Received data not in expected format:", line)
                    else:
                        self.update_status("Serial port is not open.")
                        return  # Exit function if serial port is not open or device is disconnected
                # Calculate average values
                calibration_data = [cal_val / nsamples for cal_val in calibration_data]

                # Apply scaling
                self.calibration_values = [cal_val / calibration_weight for cal_val in calibration_data]
                print("Calibration values:", self.calibration_values)
                self.update_status("Calibrated!")

        except serial.SerialException as e:
            print("Serial error:", e)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    root = tk.Tk()
    app = WalkerMonitorApp(root)
    app.run()
