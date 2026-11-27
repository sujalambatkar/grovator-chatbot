"""Write knowledge/blocks/*.yaml for the 201 blocks of "block_codes_with_purpose.pdf" (the ESP32 block editor's palette).

The sheet gives, for each block: its category, its sub-category (the sensor), its picture, its text and a "Purpose". It gives NO
code. Its own footnote says the purposes are inferred from block names and typical sensor behaviour, not from Grovator
documentation, so every block is marked verified: false.
Run:  python -m ingest.import_block_sheet [--dry-run]
Existing hand-written blocks (the pulse oximeter code blocks) are not touched: generated files carry `generated: true` in
code_notes and are the only ones this script overwrites.
"""
import re
import sys
from pathlib import Path

import yaml

BLOCKS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "blocks"
PLATFORM = "ESP32"
SENSORS_CAT = "Sensors-ESP32"
SRC = {"file": "block_codes_with_purpose.pdf"}

# ---- general blocks: (title, description) per category, in sheet order -------------------------------------------------
GENERAL: dict[str, list[tuple[str, str]]] = {
    "Functions": [
        ("To do something (function)", "Defines a function: a named group of blocks you can run again and again. Rename 'do something' and put the blocks to repeat inside it."),
        ("To do something, with return (function)", "Defines a function that gives a value back: it has a 'return' slot at the bottom for the result."),
        ("If ... return", "Inside a function: if the condition is true, stop and return the value you plug into the 'return' slot."),
    ],
    "Logic": [
        ("If ... do", "Runs the blocks inside it only when the condition plugged into 'if' is true. The gear icon adds 'else if' and 'else' parts."),
        ("Comparison (=)", "Compares two values and is true or false. Pick =, not equal, <, <=, > or >= in the dropdown."),
        ("And / Or", "Combines two conditions: 'and' is true when both are true, 'or' when at least one is (dropdown)."),
        ("Not", "Turns true into false and false into true."),
        ("True / False", "A fixed true or false value (choose in the dropdown)."),
        ("Null", "An empty value: 'nothing'."),
    ],
    "Mobile": [
        ("Connect to mobile", "Connects the board to the mobile app (the other Mobile blocks send and receive messages and data through it)."),
        ("Check Message", "Checks whether a message has arrived from the mobile app."),
        ("Mode (Mobile)", "A value block with a dropdown (Mobile is selected in the sheet). The sheet does not say more."),
        ("Get Sensor Data", "A value block with one slot for a sensor. The sheet gives only its name."),
        ("Print text to mobile: Message", "Shows the text you type on the mobile app."),
        ("Print text to mobile: [value] with color", "Shows a value you plug in (a variable or sensor reading) on the mobile app, in the colour you choose."),
        ("Print text to mobile: Message with color", "Shows the text you type on the mobile app, in the colour you choose."),
        ("Is data received from mobile for: key", "True when data was received from the mobile app for the key you type."),
        ("Read data from mobile for: key", "Gives the data the mobile app sent for the key you type."),
        ("Erase mobile data at: key", "Deletes the stored mobile data for the key you type."),
        ("Send all messages to mobile", "Sends the messages that are waiting to the mobile app."),
        ("Get input from Mobile and Machine", "Gets input from the mobile app and the machine. It has a 'Set Variable' slot and a 'GPIO' slot; the sheet does not say more."),
    ],
    "Loops": [
        ("Repeat n times", "Repeats the blocks inside it the number of times you type (5 by default)."),
        ("Repeat while / until", "Repeats the blocks inside it while (or until, dropdown) the condition is true."),
        ("Break out of loop", "Leaves the loop (or skips to the next round: choose in the dropdown)."),
    ],
    "Math": [
        ("Number", "A number value you type."),
        ("Arithmetic (+, -, x, /, power)", "Calculates with two numbers: choose the operation in the dropdown."),
        ("Square root / absolute / ...", "One-number maths such as square root (dropdown for others)."),
        ("Trigonometry (sin, cos, tan ...)", "Sine, cosine, tangent and friends of an angle in degrees (dropdown)."),
        ("Constants (pi ...)", "Gives a maths constant such as pi (dropdown)."),
        ("Number is even / odd / prime ...", "True or false test about a number (dropdown)."),
        ("Round (round, round up, round down)", "Rounds a number."),
        ("Sum of list (sum, min, max, average ...)", "Calculates one value from a list of numbers (dropdown)."),
        ("Remainder of a / b", "The remainder after dividing the first number by the second."),
        ("Constrain value low high", "Keeps a number between a low and a high limit."),
        ("Random integer from a to b", "A random whole number between the two numbers you give."),
        ("Random fraction", "A random number between 0 and 1."),
        ("Mean of (list)", "The average of a list of numbers (dropdown for other statistics)."),
        ("Correlation coefficient of (list) and (list)", "Measures how strongly two lists of numbers move together."),
        ("Round off number till number of digits", "Rounds a number to the number of digits you give."),
        ("Round column by digits from dataframe", "Rounds the numbers of a chosen column of a data table (dataframe) to the digits you give."),
        ("Round off by digits", "Rounds a number to the digits you give."),
    ],
    "Text": [
        ("Text value", "A piece of text you type between the quotes."),
        ("Create text with", "Joins several values into one text. The gear icon adds more slots."),
        ("To item append text", "Adds text to the end of a variable's text."),
        ("Length of text", "Gives the number of characters in a text."),
        ("Text is empty", "True when the text has no characters."),
        ("In text find occurrence of text", "Finds where a piece of text first (or last) appears inside another text."),
        ("In text get letter", "Gives one letter of a text, chosen by its number (or from the end, or random)."),
        ("In text get substring", "Gives a part of a text from one letter number to another."),
        ("To UPPER CASE / lower case / Title Case", "Changes the capital letters of a text (dropdown)."),
        ("Trim spaces from text", "Removes the spaces from the start, the end or both sides of a text."),
        ("Print text", "Prints a text (or any value) to the console."),
        ("Prompt for text / number with message", "Asks the user for a text or a number, showing your message."),
        ("Replace old text with new text from text", "Replaces every piece of old text inside a text with the new text."),
    ],
    "Variables": [
        ("Set variable to", "Stores a value in a variable. Pick or create the variable in the dropdown."),
        ("Change variable by", "Adds a number (1 by default) to a variable."),
        ("Variable", "Gives the current value of a variable."),
    ],
    "Common Actions-ESP32": [
        ("Use [Sensor]", "Chooses which sensor the program uses (dropdown, for example Alcohol Sensor). It sits at the top of the program."),
        ("Connect to Bluetooth", "Connects the ESP32 to a Bluetooth device."),
        ("Start", "Marks where your program begins. It is only there to make the blocks easier to read: the program works even without it."),
        ("Exit program", "Stops the program."),
        ("Get Input from Pin", "Reads the value on the GPIO pin whose number you give (0 by default)."),
        ("Pin number (dropdown)", "A pin number to plug into blocks that need a pin; choose it in the dropdown."),
        ("Pause for n second(s)", "Waits for the number of seconds you type (1 by default) before the next block runs."),
        ("Pause for [value] second(s)", "Waits for the number of seconds you plug in (a number block or a variable)."),
        ("Function Name", "Calls a function you defined: choose its name."),
        ("Print text to Bluetooth: Message with color", "Sends the text you type over Bluetooth, in the colour you choose."),
        ("Print text to Bluetooth: [value] with color", "Sends a value you plug in over Bluetooth, in the colour you choose."),
    ],
}

# ---- sensor blocks: sub-category -> (sensor file id or None, [titles]) ----------------------------------------------------
SENSOR_BLOCKS: dict[str, tuple[str | None, list[str]]] = {
    "Alcohol Sensor": ("alcohol_sensor", ["Set Alcohol Sensor as Input at Pin", "Get Alcohol Sensor Value from Pin"]),
    "BME Sensor": ("bme_sensor", ["Calculate BME Data", "Get Temperature", "Get Humidity", "Get Pressure", "Get Gas"]),
    "Button": (None, ["Set Button as Input at Pin"]),
    "Buzzer": ("buzzer", ["Set Buzzer as Output at Pin", "Turn On/Off Buzzer", "Set Buzzer (named) as Output at Pin", "Turn On/Off Buzzer (named)"]),
    "Carbon Monoxide Sensor": ("co_sensor", ["Calculate CO value in ppm", "Read CO Data in ppm with Analog Input Channel", "Set Carbon Monoxide Sensor as input at Pin"]),
    "Climate Sensor": ("dht11_climate_sensor", ["Set Climate Sensor as Input at Pin", "Read Climate Data", "Get Climate Sensor Value from Pin", "Get Temperature", "Get Humidity"]),
    "Color Sensor": ("color_sensor", ["Get Clear Data", "Get Red Data", "Get Green Data", "Get Blue Data"]),
    "Compass Sensor": ("compass_sensor", ["Compass Axis (X / Y / Z)"]),
    "Current Sensor": ("current_sensor", ["Set Current Sensor as input at Pin", "Read Current"]),
    "ESP Camera": ("esp_camera", ["Take Picture with GPIO as Tx and Rx"]),
    "Flame Sensor": ("flame_sensor", ["Set Flame Sensor as Input at Pin", "Get Flame Sensor Value from Pin"]),
    "Flow Sensor": ("flow_sensor", ["Calculate Flow Sensor Data with pin", "Read flow rate in L/min", "Read Volume in L"]),
    "GSR Sensor": ("gsr_sensor", ["Set GSR Sensor as input at Pin", "Read GSR Data with Analog Input Channel"]),
    "Hall Effect Sensor": ("hall_effect_sensor", ["Set Hall Effect Sensor as Input at Pin", "Get Hall Effect Sensor Value from Pin"]),
    "IR Sensor": ("ir_sensor", ["Set IR Sensor as Input at Pin", "Get IR Sensor Value from Pin"]),
    "Keypad": ("keypad", ["Read key value"]),
    "LCD Display": ("lcd_display", ["Display Text in Row", "Clear LCD Display"]),
    "LDR Sensor": ("ldr_sensor", ["Set LDR Sensor as Input at Pin", "Get LDR Sensor Value from Pin"]),
    "LED Board": ("led_module", ["Set LED as Output at Pin", "Turn On/Off LED"]),
    "LoRa": (None, ["LoRa Init (RX Pin, TX Pin, Band, Address, Network ID)", "LoRa Send (To Address, Data)", "Set variable LoRa Read Data"]),
    "Lux Sensor": (None, ["Get Lux Value"]),
    "Metal Detector Sensor": ("metal_detection_sensor", ["Set Metal Detector Sensor as Input at Pin", "Get Metal Effect Sensor Value from Pin"]),
    "Motion Sensor": ("motion_sensor", ["Set Motion Sensor as Input at Pin", "Get Motion Sensor Value from Pin"]),
    "Motor Controller": ("dc_motor", ["Set Motor as Output at Pin", "Turn On/Off Motor"]),
    "Motor Controller with Speed": ("dc_motor", ["Set pin as Speed Control Pin at GPIO", "Set Motor Speed to", "Set pin as Output Pin at GPIO", "Set pin On/Off",
                                                 "Set motor 1 (two pins) as Speed Control Pin at GPIO", "Set motor 1 (two pins) speed to"]),
    "MPU6050 Sensor": ("gyroscope_sensor", ["Initialize MPU6050 sensor", "Read acceleration in X/Y/Z", "Read gyro in X/Y/Z"]),
    "MQ-2 Sensor": ("gas_sensor_analog", ["Set MQ-2 Sensor as input at Pin", "Read MQ-2 Data with Analog Input Channel"]),
    "MQ-135 Sensor": ("air_quality_sensor", ["Set MQ-135 Sensor as input at Pin", "Read MQ-135 Data with Analog Input Channel"]),
    "NPK Sensor": ("npk_sensor", ["Set NPK Sensor as Tx at Pin and Rx at Pin", "Get Nitrogen", "Get Phosphorus", "Get Potassium"]),
    "PH Sensor V2.0": ("ph_sensor_v2", ["Set pH Sensor as input at Pin", "Read pH Data with Analog Input Channel"]),
    "PIR Sensor": ("motion_sensor", ["Set PIR Sensor as Input at Pin", "Get PIR Sensor Value from Pin"]),
    "Power Meter Sensor": ("power_meter", ["Get Voltage", "Get Current", "Get Power", "Get Energy", "Get Frequency", "Get Power Factor"]),
    "Pulse Oximeter": ("pulseoximeter_max30102", ["Initialize max30100", "Store Heartrate and Oximeter Data", "Get heartrate", "Get SpO2"]),
    "Pulseoximeter V2.0": ("pulseoximeter_v2_uart", ["Get SpO2", "Get Heartrate"]),
    "Pulseoximeter V3.0": (None, ["Store Heartrate and Oximeter Data", "Get spO2", "Get Heartrate"]),
    "Raindrop Sensor": ("raindrop_sensor", ["Set Raindrop Sensor as Input at Pin", "Get Raindrop Sensor Value from Pin"]),
    "Rainfall Sensor": ("rainfall_sensor", ["Get Run time", "Get Rainfall in mm", "Get Raw data"]),
    "Relay": ("relay_module", ["Set Relay as Output at Pin", "Turn On/Off Relay"]),
    "RFID": ("rfid_sensor", ["Get RFID"]),
    "Servo Motor": ("servo_motor", ["Set Servo Motor as Output at Pin (Variable name)", "Change Servo Duty Cycle"]),
    "Soil Moisture": ("soil_moisture_sensor", ["Set Moisture Sensor as Input at Pin", "Get Moisture Sensor Value from Pin"]),
    "Soil Sensor": ("soil_sensor", ["Set Soil Sensor as Tx at Pin and Rx at Pin", "Get Temperature", "Get Moisture", "Get Electrical Conductivity", "Get pH"]),
    "Sound Sensor": ("sound_sensor", ["Set Sound Sensor as Input at Pin", "Get Sound Sensor Value from Pin"]),
    "Sound Sensor Analog": ("sound_sensor_analog", ["Read Sound Data with Analog input Channel", "Set Sound Sensor as Analog input at Pin"]),
    "Spectral Sensor": ("spectral_sensor", ["Read Spectral Sensor Data", "Get colour Data (violet ...)"]),
    "TDS Sensor": ("tds_sensor", ["Set TDS Sensor as Input at Pin", "Calculate TDS Value-function", "Read TDS Data with Analog Input Pin"]),
    "Thermal Sensor": ("thermal_sensor", ["Read Thermal Value"]),
    "Ultrasonic Sensor": ("ultrasonic_sensor", ["Set Ultrasonic Sensor as Echo Input at Pin and Trigger Output at Pin", "Calculate Distance (Logic)", "Get Distance with Echo and Trigger Pin"]),
    "Vibration Sensor": ("vibration_sensor", ["Set Vibration Sensor as input at Pin", "Read Vibration sensor Data with Analog input Channel"]),
    "Voltage Sensor": ("voltage_sensor", ["Set Voltage Sensor as input at Pin", "Calculate Voltage value Function", "Read Voltage Data with Analog input Channel"]),
    "Weight Sensor": ("weight_sensor", ["Set Weight Sensor as input with pin DT and SCK", "Get Weight"]),
    "Wind Sensor": ("wind_speed_sensor", ["Set Wind Sensor as input at Pin", "Read Wind Sensor Data"]),
    "Wind Direction Sensor": ("wind_direction_sensor", ["Set Wind Direction Sensor as input at Pin", "Read Wind Direction angle with analog input channel"]),
}

# ---- "Purpose" column of the sheet, in the same order as the titles above ------------------------------------------------
GENERAL_PURPOSE: dict[str, list[str]] = {
    "Functions": ["Defines a reusable function with no return value. Put the steps inside; the gear lets you add inputs, the ? icon holds help text.",
                  "Defines a reusable function that hands a value back to whoever calls it.",
                  "Inside a function: if the condition is true, immediately return the given value and stop the function."],
    "Logic": ["Runs the blocks inside 'do' only when the condition is true. The gear adds else-if / else branches.",
              "Compares two values (=, \u2260, <, \u2264, >, \u2265) and gives true or false.",
              "Combines two conditions with AND or OR; gives true/false.",
              "Flips a true/false value (true becomes false and vice versa).",
              "A fixed true or false value (switch with the dropdown).",
              "An empty / 'no value' placeholder."],
    "Mobile": ["Starts the connection between the ESP32 board and the mobile app.",
               "Checks whether a new message has arrived from the mobile app.",
               "Selects the mode for mobile communication (value chosen by dropdown); outputs it as a value.",
               "Fetches the sensor data / value the mobile side needs, using the connected input.",
               "Sends a text message to the mobile app to be displayed.",
               "Sends the value plugged in to the mobile app, shown in the chosen colour.",
               "Sends a typed message to the mobile app, shown in the chosen colour.",
               "True if the mobile app has sent data under the given key name.",
               "Reads the value the mobile app sent for the given key.",
               "Clears the stored mobile data for the given key.",
               "Pushes all queued messages to the mobile app at once.",
               "Takes an input from the mobile app and a machine/GPIO pin, storing it in the chosen variable for the given GPIO pin."],
    "Loops": ["Repeats the blocks inside a fixed number of times.",
              "Repeats the inner blocks while (or until, via dropdown) the condition holds.",
              "Exits the loop immediately, or skips to the next round ('continue') via dropdown."],
    "Math": ["A number value.", "Arithmetic on two numbers (+, \u2212, \u00d7, \u00f7, power).",
             "Applies a maths function to a number: square root, absolute, \u2212, ln, log10, e^, 10^.",
             "Trigonometric function (sin, cos, tan, asin, acos, atan) of an angle in degrees.",
             "A mathematical constant (\u03c0, e, \u03c6, \u221a2, \u221a\u00bd, \u221e) chosen by dropdown.",
             "Tests a number (even, odd, prime, whole, positive, negative, divisible by); gives true/false.",
             "Rounds a number (round, round up, round down).",
             "Aggregate of a list of numbers: sum, min, max, average, median, mode, std deviation, random item.",
             "Gives the remainder after dividing the first number by the second (modulo).",
             "Forces a number to stay within the low\u2013high range; values outside are clamped to the limit.",
             "Gives a random whole number between the two limits.", "Gives a random decimal between 0 and 1.",
             "Statistic of a list/data (mean and similar options) for data analysis.",
             "Calculates how strongly two data lists are correlated.",
             "Rounds a number to the given count of decimal places.",
             "Rounds all values in a column (or the whole table) of a dataframe to the given decimals.",
             "Rounds the connected value to the given number of digits."],
    "Text": ["A piece of text (string) you can type into.", "Joins several values/texts into one text (gear adds more inputs).",
             "Adds text onto the end of a text variable.", "Gives the number of characters in the text.", "True if the text has no characters.",
             "Gives the position of the first (or last) occurrence of a piece of text inside another text; 0 if not found.",
             "Gets one letter at a position (from start, from end, first, last or random).", "Extracts part of a text between two positions.",
             "Changes text case: UPPER CASE, lower case or Title Case.", "Removes spaces from both ends, or just the left or right.",
             "Outputs the text/value (to the serial monitor / console).", "Asks the user to type in text (or a number) with the message shown.",
             "Replaces every occurrence of the old text with new text inside the given text."],
    "Variables": ["Stores a value in the chosen variable.", "Adds a number to the variable's current value (use a negative number to subtract).",
                  "Variable getter: gives the current value stored in the variable."],
    "Common Actions-ESP32": ["Declares which sensor (chosen by dropdown) the program uses, so its library/setup is included.",
                             "Starts the Bluetooth connection of the ESP32.", "Marks where your program begins. It is only there to make the blocks easier to read: the program works even without it.", "Stops the program.",
                             "Reads the digital input value from the chosen pin number.", "A pin number picked from a dropdown, for plugging into other blocks.",
                             "Waits the typed number of seconds before moving on.", "Waits for the number of seconds supplied by the connected block/variable.",
                             "Calls a function you defined (edit the name to match).",
                             "Sends a typed message over Bluetooth to a paired device in the chosen colour.", "Sends the connected value over Bluetooth in the chosen colour."],
}

SENSOR_PURPOSE: dict[str, list[str]] = {
    "Alcohol Sensor": ["Sets which pin the alcohol sensor is connected to and prepares it as an input.", "Reads the alcohol sensor's value from the given pin."],
    "BME Sensor": ["Triggers the BME sensor to take a fresh reading (call before reading values).", "Returns the BME sensor's temperature reading.",
                   "Returns the BME sensor's humidity reading.", "Returns the BME sensor's air-pressure reading.", "Returns the BME sensor's gas (air quality) reading."],
    "Button": ["Sets the pin the push button is wired to as an input."],
    "Buzzer": ["Sets the pin the buzzer is wired to as an output.", "Switches the buzzer on or off.",
               "Sets a numbered/named buzzer on a given pin as an output (for projects with several buzzers).", "Switches the specified buzzer on or off."],
    "Carbon Monoxide Sensor": ["Converts the carbon monoxide sensor's raw reading into ppm (parts per million).",
                               "Reads the CO sensor on an analog channel and gives the value in ppm.", "Sets the pin the carbon monoxide sensor is connected to as input."],
    "Climate Sensor": ["Sets the pin the temperature/humidity sensor is connected to.", "Takes a fresh reading from the climate sensor.",
                       "Reads the climate sensor's value from the given pin.", "Returns the temperature from the last climate reading.", "Returns the humidity from the last climate reading."],
    "Color Sensor": ["Returns the colour sensor's clear (overall brightness) channel.", "Returns the red channel value from the colour sensor.",
                     "Returns the green channel value from the colour sensor.", "Returns the blue channel value from the colour sensor."],
    "Compass Sensor": ["Returns the compass heading / magnetic field on the chosen axis (X, Y or Z)."],
    "Current Sensor": ["Sets the pin the current sensor is connected to.", "Returns the measured electrical current."],
    "ESP Camera": ["Captures a picture with the ESP camera module using the chosen Tx/Rx GPIO pins."],
    "Flame Sensor": ["Sets the pin the flame sensor is connected to.", "Reads the flame sensor's value (detects fire/flame) from the pin."],
    "Flow Sensor": ["Sets up and processes the water-flow sensor pulses on the given pin.", "Returns the flow rate in litres per minute.", "Returns the total volume that has flowed, in litres."],
    "GSR Sensor": ["Sets the pin the GSR (skin conductance) sensor is connected to.", "Reads galvanic skin response on an analog channel."],
    "Hall Effect Sensor": ["Sets the pin the Hall effect (magnetic field) sensor is connected to.", "Reads the Hall effect sensor's value (detects a magnet nearby)."],
    "IR Sensor": ["Sets the pin the infrared sensor is connected to.", "Reads the IR sensor's value (obstacle/reflection detection)."],
    "Keypad": ["Returns the key pressed on the keypad."],
    "LCD Display": ["Shows the given text on the chosen row of the LCD.", "Erases everything on the LCD."],
    "LDR Sensor": ["Sets the pin the LDR (light-dependent resistor) is connected to.", "Reads the light level from the LDR."],
    "LED Board": ["Sets a given LED on a given pin as an output.", "Switches the specified LED on or off."],
    "LoRa": ["Initialises the LoRa radio module: pins used, frequency band (865 MHz default), this device's address and the network ID.",
             "Sends data over LoRa to the device with the given address.", "Stores the received LoRa data into the chosen variable."],
    "Lux Sensor": ["Returns the ambient light level in lux."],
    "Metal Detector Sensor": ["Sets the pin the metal detector is connected to.", "Reads the metal detector's value (detects metal nearby). Label says 'Metal Effect' in the IDE."],
    "Motion Sensor": ["Sets the pin the motion sensor is connected to.", "Reads the motion sensor's value (motion detected or not)."],
    "Motor Controller": ["Sets a given motor on a given pin as an output.", "Switches the specified motor on or off."],
    "Motor Controller with Speed": ["Assigns a GPIO pin used for PWM speed control of a motor.", "Sets the specified motor's speed (value, e.g. 0\u2013100).",
                                    "Assigns a GPIO pin as a motor output (direction/enable) pin.", "Turns the chosen motor pin on or off.",
                                    "Sets up both speed-control pins of motor 1 in a single block.", "Sets the speed of both channels of motor 1 in a single block."],
    "MPU6050 Sensor": ["Starts the MPU6050 accelerometer/gyroscope so it can be read.", "Returns acceleration on the chosen axis (X, Y or Z).", "Returns rotation rate on the chosen axis (X, Y or Z)."],
    "MQ-2 Sensor": ["Sets the pin the MQ-2 gas/smoke sensor is connected to.", "Reads the MQ-2 gas/smoke level on an analog channel."],
    "MQ-135 Sensor": ["Sets the pin the MQ-135 air-quality sensor is connected to.", "Reads the MQ-135 air-quality level on an analog channel."],
    "NPK Sensor": ["Sets the serial (Tx/Rx) pins used to talk to the soil NPK sensor.", "Returns the soil nitrogen level.", "Returns the soil phosphorus level.", "Returns the soil potassium level."],
    "PH Sensor V2.0": ["Sets the pin the pH sensor is connected to.", "Reads the pH value on an analog channel."],
    "PIR Sensor": ["Sets the pin the PIR (human motion) sensor is connected to.", "Reads whether the PIR sensor detects movement."],
    "Power Meter Sensor": ["Returns the measured voltage.", "Returns the measured current.", "Returns the measured power.", "Returns the accumulated energy consumption.",
                           "Returns the AC line frequency.", "Returns the power factor of the load."],
    "Pulse Oximeter": ["Starts the MAX30100 pulse oximeter / heart-rate sensor.", "Saves the heart rate and SpO2 readings into the connected variables.",
                       "Returns the stored heart rate.", "Returns the stored blood-oxygen (SpO2) level."],
    "Pulseoximeter V2.0": ["Returns the blood-oxygen (SpO2) level from the V2.0 sensor.", "Returns the heart rate from the V2.0 sensor."],
    "Pulseoximeter V3.0": ["Saves the heart rate and SpO2 readings into the connected variables (V3.0 sensor).", "Returns the SpO2 level from the V3.0 sensor.", "Returns the heart rate from the V3.0 sensor."],
    "Raindrop Sensor": ["Sets the pin the raindrop sensor is connected to.", "Reads the raindrop sensor's value (detects rain/water drops)."],
    "Rainfall Sensor": ["Returns how long the rain gauge has been running.", "Returns the measured rainfall in millimetres.", "Returns the raw rain-gauge reading."],
    "Relay": ["Sets a given relay on a given pin as an output.", "Switches the specified relay on or off."],
    "RFID": ["Returns the ID read from an RFID tag/card."],
    "Servo Motor": ["Attaches a servo motor to a pin and gives it a variable name.", "Moves the named servo by setting its duty cycle (controls the angle)."],
    "Soil Moisture": ["Sets the pin the soil-moisture sensor is connected to.", "Reads the soil moisture value."],
    "Soil Sensor": ["Sets the serial (Tx/Rx) pins used to talk to the multi-parameter soil sensor.", "Returns the soil temperature.", "Returns the soil moisture.",
                    "Returns the soil electrical conductivity (EC, salinity indicator).", "Returns the soil pH."],
    "Sound Sensor": ["Sets the pin the sound sensor is connected to.", "Reads the sound sensor's value from the pin."],
    "Sound Sensor Analog": ["Reads the sound level on an analog channel.", "Sets the pin the analog sound sensor is connected to as an analog input."],
    "Spectral Sensor": ["Takes a reading from the spectral (light colour spectrum) sensor.", "Returns the intensity of the chosen wavelength band (violet, blue, etc.) from the last reading."],
    "TDS Sensor": ["Sets the pin the TDS (water quality) sensor is connected to.", "Converts the raw TDS reading into a TDS value (ppm).", "Reads the TDS value on an analog pin."],
    "Thermal Sensor": ["Returns the temperature value from the thermal sensor."],
    "Ultrasonic Sensor": ["Sets the echo (input) and trigger (output) pins of the ultrasonic distance sensor.", "Runs the logic that measures the echo time and works out the distance.",
                          "Returns the distance measured by the ultrasonic sensor."],
    "Vibration Sensor": ["Sets the pin the vibration sensor is connected to.", "Reads the vibration level on an analog channel."],
    "Voltage Sensor": ["Sets the pin the voltage sensor is connected to.", "Converts the raw reading into an actual voltage value.", "Reads the voltage on an analog channel."],
    "Weight Sensor": ["Sets the data (DT) and clock (SCK) pins of the load-cell amplifier (HX711).", "Returns the measured weight."],
    "Wind Sensor": ["Sets the pin the wind-speed sensor is connected to.", "Returns the wind speed reading."],
    "Wind Direction Sensor": ["Sets the pin the wind-direction sensor is connected to.", "Reads the wind direction as an angle from an analog channel."],
}

# the sheet's own summary (pages 11-12): the importer refuses to run if the lists above disagree with it
EXPECTED_GENERAL = {"Functions": 3, "Logic": 6, "Mobile": 12, "Loops": 3, "Math": 17, "Text": 13, "Variables": 3, "Common Actions-ESP32": 11}
EXPECTED_TOTAL = 201

ALIASES = {"Pause for n second(s)": ["pause", "wait", "delay"], "Pause for [value] second(s)": ["pause", "wait", "delay"], "Start": ["start block"],
           "Exit program": ["exit"], "Repeat n times": ["repeat", "loop"], "Repeat while / until": ["repeat while", "while loop"], "Break out of loop": ["break"]}


def slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", " ".join(parts).lower()).strip("_")[:90]


def build() -> list[dict]:
    for cat, n in EXPECTED_GENERAL.items():
        assert len(GENERAL[cat]) == n, f"{cat}: {len(GENERAL[cat])} blocks, the sheet says {n}"
    rows: list[dict] = []
    order = 0

    def add(cat: str, sub: str, title: str, desc: str, sensor: str | None, verified: bool) -> None:
        nonlocal order
        order += 1
        rows.append({"id": slug(sub or cat, title), "title": title, "category": cat, "sub_category": sub, "platform": PLATFORM, "order": order,
                     "aliases": ALIASES.get(title, []), "sensor": sensor, "description": desc, "verified": verified, "code_notes": ["generated: true (from block_codes_with_purpose.pdf)"],
                     "sources": [{**SRC}]})

    for cat, items in GENERAL.items():
        assert len(GENERAL_PURPOSE[cat]) == len(items), f"{cat}: purposes do not match titles"
        for (title, _old), purpose in zip(items, GENERAL_PURPOSE[cat]):
            add(cat, "", title, purpose, None, False)
    for sub, (sensor, titles) in SENSOR_BLOCKS.items():
        assert len(SENSOR_PURPOSE[sub]) == len(titles), f"{sub}: purposes do not match titles"
        for t, purpose in zip(titles, SENSOR_PURPOSE[sub]):
            add(SENSORS_CAT, sub, t, purpose, sensor, False)
    ids = [r["id"] for r in rows]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, f"duplicate ids: {sorted(dup)}"
    assert len(rows) == EXPECTED_TOTAL, f"{len(rows)} blocks, the sheet says {EXPECTED_TOTAL}"
    return rows


def main(argv: list[str]) -> int:
    rows = build()
    if "--dry-run" in argv:
        print(f"{len(rows)} blocks would be written")
        return 0
    BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
    for old in BLOCKS_DIR.glob("*.yaml"):  # refresh: drop only the files this script generated earlier
        if "generated: true (from block_codes_with_purpose.pdf)" in old.read_text(encoding="utf-8"):
            old.unlink()
    for r in rows:
        (BLOCKS_DIR / f"{r['id']}.yaml").write_text(yaml.safe_dump(r, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    print(f"wrote {len(rows)} block files to {BLOCKS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
