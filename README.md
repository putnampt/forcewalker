# Walker Force Monitor

A Python GUI application for monitoring and recording force data from a walker equipped with four force sensors on the arm pads. This tool is designed for research purposes to analyze forces applied during walker-assisted ambulation.

## ⚠️ Important Disclaimer

**THIS SOFTWARE IS FOR RESEARCH PURPOSES ONLY.** Users assume all risks and should thoroughly understand the code, calibration procedures, and hardware before trusting any data collected. The authors are not responsible for any consequences resulting from the use of this software.

## Features

- **Real-time Force Monitoring**: Displays live force data from four sensors (Right-Rear, Right-Front, Left-Rear, Left-Front)
- **Data Recording**: Record force measurements with timestamps
- **Calibration System**: Tare (zero) and calibrate sensors with known weights
- **Data Export**: Save data in both HDF5 (.h5) and Excel (.xlsx) formats
- **Data Visualization**: Plot recorded force data over time
- **Serial Communication**: Interface with Arduino-based force sensing hardware
- **Bluetooth Support**: Framework for connecting to additional sensors (currently disabled)

## Hardware Requirements

- Walker equipped with four force sensors on arm pads
- Arduino or compatible microcontroller for sensor data acquisition
- Serial connection (USB) between computer and Arduino
- Optional: Bluetooth-enabled force sensors (GDX-HD devices)

## Software Requirements

### Python Dependencies

```bash
pip install tkinter
pip install pyserial
pip install pandas
pip install pillow
pip install h5py
pip install numpy
pip install matplotlib
pip install nest-asyncio
```

### Additional Requirements

- **gdx library**: For Bluetooth sensor communication (if using GDX-HD devices)
- **app_data/splash.png**: Application splash image (400x400 pixels recommended)

## Installation

1. Clone or download the application files
2. Install required Python packages using pip
3. Ensure the `app_data/splash.png` file is present in the application directory
4. Connect your force-sensing walker hardware to the computer via serial

## Usage

### Starting the Application

```bash
python walker_monitor.py
```

### Basic Operation Flow

1. **Connect Hardware**
   - Select the appropriate serial port from the dropdown
   - Click "Connect" to establish communication with the walker sensors
   - Wait for "Ready!" status indicating successful connection

2. **Calibration** (Recommended)
   - Click "Tare" to zero the sensors (removes baseline readings)
   - For accurate measurements, calibrate with known weights using "Calibrate"

3. **Recording Data**
   - Click "Record Data" to start data collection
   - Perform walker ambulation testing
   - Click "Stop Recording" when finished

4. **Saving and Viewing Data**
   - Click "Save Data" to export recordings to Excel and HDF5 formats
   - Click "View Data" to display force plots
   - Files are saved in the `Data/` directory with timestamps

### Button Functions

- **Connect**: Establish serial communication with walker hardware
- **Record Data**: Begin force data collection
- **Stop Recording**: End data collection session
- **Save Data**: Export recorded data to files
- **Tare**: Zero all sensors (10-second averaging period)
- **Calibrate**: Calibrate sensors using known weight (5-second averaging)
- **View Data**: Display force data plots
- **Live Data**: Real-time data display (feature in development)
- **Bluetooth**: Connect to additional Bluetooth sensors (currently disabled)
- **Close**: Exit application with unsaved data warning

## Data Format

### Serial Data Input
The application expects comma-separated values from the Arduino:
```
rr_value,rf_value,lr_value,lf_value
```
Where:
- `rr_value`: Right-Rear sensor reading
- `rf_value`: Right-Front sensor reading  
- `lr_value`: Left-Rear sensor reading
- `lf_value`: Left-Front sensor reading

### Output Files

**Excel Format (.xlsx)**:
- Separate sheets for each sensor (RR, RF, LR, LF)
- Columns: Timestamp, Force Value
- Timestamp in seconds from recording start
- Force values in grams (after calibration)

**HDF5 Format (.h5)**:
- Datasets: 'rr', 'rf', 'lr', 'lf'
- Each dataset contains (timestamp, force_value) pairs
- Efficient storage for large datasets

## Calibration Procedure

### Taring (Zeroing)
1. Ensure walker is unloaded
2. Click "Tare" button
3. Application averages readings for 10 seconds
4. Baseline values are subtracted from future readings

### Weight Calibration
1. Place known weight on walker arm pads
2. Click "Calibrate" button
3. Enter the calibration weight in grams when prompted
4. Application averages readings for 5 seconds and calculates scaling factors

**Note**: Default calibration values are pre-set in the code but should be verified with your specific hardware.

## File Structure

```
walker_monitor/
├── walker_monitor.py          # Main application file
├── app_data/
│   └── splash.png            # Application logo/splash image
├── Data/                     # Output directory (created automatically)
│   ├── FW_YYYY-MM-DD_HH-MM-SS.xlsx
│   └── FW_YYYY-MM-DD_HH-MM-SS.h5
└── README.md
```

## Arduino Communication Protocol

The Arduino should send data at 57600 baud with the following protocol:

**Startup Sequence**:
```
Starting...
[sensor initialization]
Finished Setup!
```

**Data Stream**:
```
rr_value,rf_value,lr_value,lf_value
rr_value,rf_value,lr_value,lf_value
...
```

## Troubleshooting

### Connection Issues
- Verify correct serial port selection
- Check Arduino is powered and programmed correctly
- Ensure 57600 baud rate matches Arduino configuration
- Try disconnecting and reconnecting USB cable

### Data Quality Issues
- Perform taring procedure with unloaded walker
- Calibrate with known weights for accurate measurements
- Check sensor connections and Arduino wiring
- Verify sensor mounting is secure

### Application Crashes
- Check all required Python packages are installed
- Ensure `app_data/splash.png` file exists
- Verify sufficient disk space for data files
- Close other applications using the same serial port

## Development Notes

- Threading is used for serial communication to prevent GUI freezing
- Bluetooth functionality is currently commented out pending further development
- The application includes safety checks for unsaved data
- All data is stored in memory during recording sessions

## Future Enhancements

- Live data plotting during recording
- Enhanced Bluetooth sensor integration
- Additional data analysis tools
- Improved error handling and user feedback
- Support for different sensor configurations

## License

This software is provided as-is for research purposes. Users are responsible for validating results and ensuring safe operation with their specific hardware configuration.

## Support

This is research software provided without warranty. Users should have sufficient technical knowledge to understand and modify the code as needed for their specific applications.