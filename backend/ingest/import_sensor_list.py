"""One-off import of "Sensor Details and Pin Configurations - Sensor List.pdf" (50 sensors, Raspberry Pi controller wiring)
plus the ESP32 gateway JSON (knowledge_src/hardware) into knowledge/sensors/<id>.yaml and knowledge/kits/*.yaml.

    python -m ingest.import_sensor_list            # writes/overwrites the generated files (sensors with `generated: true` in notes)

The data table below is the PDF, transcribed (the "working" texts are shortened, nothing was added). After the import the YAML
files are the source of truth: edit them, not this script. A sensor file that has no marker (e.g. the lesson's own pulse oximeter)
is never overwritten.
"""
import json
import re
from pathlib import Path

import yaml

from knowledge.loader import KNOWLEDGE_DIR

PDF = "Sensor Details and Pin Configurations - Sensor List.pdf"
CTRL, ESP = "govator_controller_pi", "esp32_kit"
GW = Path(__file__).resolve().parents[1] / "knowledge_src" / "hardware" / "grovator-esp32-gateway.json"
ANY_DIG = "any digital GPIO pin of the controller"


def d3(sig, v="3.3V or 5V"):
    return [("VCC", v), ("GND", "Ground"), (sig, ANY_DIG)]


def ana(v="3-5V"):
    return [("VCC", v), ("GND", "GND"), ("Signal", "Analog input (AI1, AI2, AI3, AI4)")]


I2C = [("VCC", "3.3 or 5V"), ("GND", "Ground"), ("SDA", "any SDA pin of the controller"), ("SCL", "any SCL pin of the controller")]
UART = [("VCC", "3.3V or 5V"), ("GND", "Ground"), ("Rx", "Tx of the controller"), ("Tx", "Rx of the controller")]
UART_RR = [("VCC", "3.3-5V"), ("GND", "Ground"), ("RX", "RX"), ("TX", "TX")]

# id, name, category, type, gateway key, extra aliases, working, controller pins, controller note, example code
T = [
 ("alcohol_sensor", "Alcohol Sensor", "Input", "digital", "alcohol_sensor", [], "Detects alcohol vapour in the air. The sensing element's resistance changes with alcohol; a built-in comparator with an adjustable potentiometer threshold gives a digital HIGH when the alcohol level is above the threshold and LOW otherwise.", d3("DO"), "", "Alcohol_sensor.py.py"),
 ("flame_sensor", "Flame Sensor", "Input", "digital", "flame_sensor", [], "Detects the infrared light given off by a flame. An onboard comparator (adjustable with the potentiometer) gives a digital HIGH when a flame is detected and LOW when not.", d3("D0"), "", "flame_sensor.py.py"),
 ("hall_effect_sensor", "Hall Effect Sensor", "Input", "digital", "hall_effect_sensor", ["hall sensor", "hall effect"], "Detects a magnetic field: when a magnet comes near, it produces a signal, and a comparator gives a digital output. The sensor list's text contradicts itself about whether the output is HIGH or LOW when a magnet is detected (see GAPS.md).", d3("D0"), "", "Hall_effect_sensor.py.py"),
 ("ir_sensor", "IR Sensor", "Input", "digital", "ir_sensor", ["infrared sensor", "obstacle sensor"], "Detects objects or obstacles. An IR LED sends light and a photodiode receives the reflection; a comparator (adjustable) gives a digital HIGH when an object is detected and LOW when not.", d3("D0"), "", "IR_sensor.py.py"),
 ("keypad", "Keypad", "Input", "digital", "keypad", ["matrix keypad"], "A matrix keypad for typing numbers or characters. A key press joins one row and one column, and the program scans the rows and columns to find the key.", [], "keypad goes to the Keypad Slot (dedicated connector)", ""),
 ("ldr_sensor", "LDR Sensor", "Input", "digital", "ldr_module", ["ldr", "light sensor", "light dependent resistor", "ldr module"], "Detects light intensity: the LDR's resistance falls in bright light and rises in the dark. A comparator (adjustable) gives a digital HIGH when the light is above the threshold and LOW when it is dark.", d3("D0"), "", "LDR_sensor.py.py"),
 ("metal_detection_sensor", "Metal Detection Sensor", "Input", "digital", "metal_detection_sensor", ["metal detector", "metal sensor"], "Detects nearby metal. It makes an electromagnetic field that a metal object disturbs; a comparator (adjustable) gives a digital HIGH when metal is detected and LOW when not.", d3("D0"), "", "Metal_detector.py.py"),
 ("soil_moisture_sensor", "Soil Moisture Sensor", "Input", "digital", "soil_moisture_sensor", ["soil moisture"], "Measures soil moisture with two probes pushed into the soil: wet soil conducts better. A comparator (adjustable) gives a digital HIGH when the soil is wet (above the threshold) and LOW when dry.", d3("D0"), "", "Moisture_sensor.py.py"),
 ("raindrop_sensor", "Raindrop Sensor", "Input", "digital", "raindrop_sensor", ["rain sensor", "rain drop sensor"], "Detects rain or water drops. Water joining the conductive tracks on the plate changes the signal; a comparator (adjustable) gives a digital HIGH when rain is detected and LOW when not.", d3("D0"), "", "raindropsensor.py.py"),
 ("sound_sensor", "Sound Sensor", "Input", "digital", "sound_sensor", ["microphone sensor"], "Detects sound level with a microphone, amplifier and comparator (adjustable). It gives a digital HIGH when the sound is above the threshold and LOW when below.", d3("D0"), "", "sound_sensor.py.py"),
 ("dht11_climate_sensor", "DHT11 Climate Sensor", "Input", "digital", "dht11", ["dht11", "dht 11", "temperature and humidity sensor", "temperature sensor", "humidity sensor"], "Measures temperature and relative humidity. It processes the readings itself and gives a calibrated digital output on one data pin, so no ADC is needed.", d3("D0"), "", "DHT11 Climate Sensor.py.py"),
 ("ultrasonic_sensor", "Ultrasonic Sensor", "Input", "digital", "ultrasonic_sensor", ["ultrasonic", "distance sensor"], "Measures distance with sound. It sends high-frequency pulses from TRIG and listens for the echo on ECHO; the program times the echo and works out the distance in centimetres.", [("VCC", "5V"), ("GND", "Ground"), ("TRIG", "any digital pin"), ("ECHO", "any digital pin")], "", "Ultrasonicsensor.py.py"),
 ("vibration_sensor", "Vibration Sensor", "Input", "analog", "vibration_sensor", [], "Measures the strength of vibrations or shocks. Its output voltage changes with the vibration, read through an analog input: more vibration gives a higher reading. (The ESP32 gateway dataset lists it as digital D0: see GAPS.md.)", ana(), "", "Vibration.py.py"),
 ("dc_motor", "DC Motor", "Output", "digital", "dc_motor", ["dc motor", "motor"], "Turns electrical energy into rotation. Applying DC voltage makes the shaft turn; reversing the polarity reverses the direction.", [("T1", "M15 / M14 / M2 / M12 pin of the controller"), ("T2", "M15 / M14 / M2 / M12 pin of the controller")], "Relay GPIO22 and GPIO23", "DCMotor.py.py"),
 ("servo_motor", "Servo Motor", "Output", "digital", "servo", ["servo", "servo motor"], "Moves to a precise angle (usually 0 to 180 degrees) set by the width of a PWM pulse from the microcontroller, and holds that angle.", [("VCC", "5V (external supply recommended)"), ("GND", "Ground"), ("Signal", "any digital GPIO pin")], "", "servomotor.py.py"),
 ("buzzer", "Buzzer", "Output", "digital", "buzzer", [], "Makes a sound. A HIGH signal turns it ON and a LOW signal turns it OFF; it can be driven directly from a digital output pin.", d3("D0"), "", "buzzer.py.py"),
 ("led_module", "LED Module", "Output", "digital", "led_module", ["led board", "led module", "three led module"], "A module with three LEDs (red, green, yellow) for visual indication. Setting the control pin (D0) HIGH turns the matching LED ON, LOW turns it OFF.", d3("D0"), "", "LED.py"),
 ("lcd_display", "LCD Display", "Output", "I2C", "i2c_lcd", ["lcd", "i2c lcd", "lcd display", "display"], "Shows text, numbers and simple characters. The microcontroller sends commands and data over I2C to put characters at given positions.", I2C, "", "LCD.py"),
 ("camera", "Camera", "Output", "USB", "camera", ["usb camera"], "A camera that plugs into a USB port.", [("USB", "USB port")], "", ""),
 ("water_pump", "Water Pump", "Output", "digital", "water_pump", ["pump"], "Pumps and circulates water. It is switched by a relay module: a HIGH control signal activates the relay and turns the pump ON, LOW turns it OFF.", [("VCC", "12V (GPIO22 and GPIO23) only, and also to any relay port"), ("GND", "GND")], "", "waterpump.py.py"),
 ("air_quality_sensor", "Air Quality Sensor", "Input", "analog", "", ["air quality", "air quality detector"], "Measures the conductivity of the air: low when the air is clean, high when it holds combustible gases.", ana(), "", "Airqualitysensor.py.py"),
 ("ecg_sensor", "ECG Sensor", "Input", "analog", "", ["ecg", "electrocardiogram sensor"], "Measures the electrical activity of the heart with electrodes on the body. The analog output voltage follows the heartbeat and draws the ECG waveform.", ana(), "", "Ecg_sensor.py.py"),
 ("gas_sensor_analog", "Gas Sensor (Analog)", "Input", "analog", "", ["gas sensor", "analog gas sensor", "mq gas sensor"], "Detects gases such as LPG, methane, propane and smoke. The sensing element's resistance changes with the gas, giving an analog voltage that shows the concentration.", ana(), "", "Gas_sensor.py.py"),
 ("gsr_sensor", "GSR Sensor", "Input", "analog", "", ["gsr", "galvanic skin response sensor"], "Measures skin conductance with two electrodes on the skin; it changes with sweat gland activity, so it can show stress or emotional arousal. Output is an analog voltage.", ana(), "", "GSR_sensor_values.py.py"),
 ("particulate_matter_sensor", "Particulate Matter Sensor", "Input", "USB", "", ["pm sensor", "dust sensor", "pm2.5 sensor", "particulate matter"], "Measures fine dust (PM1.0, PM2.5, PM10) with a laser that counts airborne particles, and sends the readings to the Raspberry Pi over USB.", [("USB", "USB port")], "", "Particulate_matter_sensor.py.py"),
 ("ph_sensor", "PH Sensor", "Input", "analog", "", ["ph sensor", "ph meter"], "Measures how acidic or alkaline a liquid is. A pH probe and signal circuit give an analog voltage that changes with the pH.", ana(), "", "PH_sensor.py.py"),
 ("ph_sensor_v2", "PH Sensor V2", "Input", "analog", "", ["ph sensor v2", "ph v2"], "The second version of the pH sensor: measures acidity or alkalinity of a liquid as an analog voltage.", ana(), "", "PH_sensor_v2.py.py"),
 ("rtc", "RTC", "-", "none", "", ["rtc", "real time clock", "real-time clock"], "A real-time clock module that keeps the date and time (seconds to year) with a backup battery, even when the Raspberry Pi is off.", [], "No connection is given in the sensor list", "RTC.py.py"),
 ("tds_sensor", "TDS Sensor", "Input", "analog", "", ["tds", "tds meter"], "Measures dissolved solids (salts, minerals) in water from the water's conductivity, as an analog voltage.", ana(), "", "TDS_sensor.py.py"),
 ("weight_sensor", "Weight Sensor", "Input", "digital", "", ["load cell", "hx711", "weighing sensor"], "A load cell that measures weight or force. A very small signal is amplified and digitised by an HX711 module and read by the Raspberry Pi.", [("VCC", "3.3V or 5V"), ("GND", "Ground"), ("DT", ANY_DIG), ("SCK", ANY_DIG)], "", "Weight_sensor.py.py"),
 ("power_meter", "Power Meter", "Input", "UART", "", ["energy meter", "power meter"], "Measures voltage, current, power, energy, frequency and power factor, and sends them over UART (RS485 or Modbus depending on the module).", UART, "The list writes 'Rx Tx / Tx Rx'; read here as crossed (sensor Rx to controller Tx, sensor Tx to controller Rx)", "Power_meter.py.py"),
 ("relay_module", "Relay", "Input", "relay", "", ["relay", "relay module"], "An electrically operated switch: a low-voltage control signal switches a high-voltage or high-current load (lights, fans, motors, pumps).", [("Right side VCC", "3 or 5V"), ("Right side GND", "GND"), ("Right side Out", "any digital GPIO pin"), ("Right side VCC (second)", "12V"), ("Right side GND (second)", "GND"), ("Left side VCC", "VCC of the load (pump, DC motor, solenoid...)"), ("Left side GND", "GND of the load")], "Written in the list as 'Right Side' and 'Left Side' connections; the layout is partly ambiguous (see GAPS.md)", "Relaywaterpump.py.py"),
 ("accelerometer", "Accelerometer", "Input", "I2C", "", ["accelerometer", "acceleration sensor"], "Measures acceleration, tilt, vibration and movement along X, Y and Z with a MEMS element, and sends the values over I2C.", I2C, "", "Accelerometer.py.py"),
 ("blood_pressure_sensor", "Blood Pressure Sensor", "Input", "UART", "", ["blood pressure"], "A pressure cuff and sensing element that follow the pressure changes while the cuff inflates and deflates, to work out systolic and diastolic blood pressure. Not a medical device.", UART, "The list writes 'Rx Tx / Tx Rx'; read here as crossed", "Blood_Pressure_sensor.py.py"),
 ("color_sensor", "Color Sensor", "Input", "I2C", "", ["colour sensor", "color sensor", "rgb sensor"], "Tells the colour of an object by measuring the red, green and blue light it reflects, and sends the values over I2C.", I2C, "", "Color_sensor.py.py"),
 ("compass_sensor", "Compass Sensor", "Input", "I2C", "", ["compass", "magnetometer"], "Measures the Earth's magnetic field to find the heading relative to magnetic north, and sends it over I2C.", I2C, "", ""),
 ("co_sensor", "CO Sensor", "Input", "analog", "", ["carbon monoxide sensor", "co gas sensor"], "Detects and measures carbon monoxide gas in the air; its output value changes with the CO level.", ana(), "", "CO_sensor.py.py"),
 ("flow_sensor", "Flow Sensor", "Input", "digital", "", ["water flow sensor"], "Measures the rate of water flow in a pipe so the amount of water used can be calculated.", d3("D0"), "", ""),
 ("gps_sensor", "GPS Sensor", "Input", "UART", "", ["gps", "gps module"], "Finds position (latitude, longitude), speed, altitude and time from GPS satellites and sends them over UART.", UART, "The list writes 'Rx Tx / Tx Rx'; read here as crossed", "GPS_sensor.py.py"),
 ("gyroscope_sensor", "Gyroscope Sensor", "Input", "I2C", "", ["gyroscope", "gyro", "gyro sensor"], "Measures angular velocity and rotation around one or more axes and sends the values over I2C (or SPI).", I2C, "", "Gyroscope.py.py"),
 ("motion_sensor", "Motion Sensor", "Input", "digital", "", ["pir sensor", "pir", "motion detector"], "Detects the presence and movement of objects or people in range (usually from infrared radiation) and sets a digital output.", d3("D0"), "", "motion_sensor.py.py"),
 ("npk_sensor", "NPK Sensor", "Input", "UART", "", ["npk", "soil nutrient sensor"], "Measures nitrogen (N), phosphorus (P) and potassium (K) in soil, sending the values over RS485 or Modbus.", UART_RR, "For the NPK sensor only, RX connects to RX and TX to TX", "NPK_Sensor.py.py"),
 ("pressure_sensor", "Pressure Sensor", "Input", "I2C", "", ["pressure sensor", "barometer"], "Measures the pressure of gases or liquids and sends it over I2C.", I2C, "", ""),
 ("rainfall_sensor", "Rain Fall Sensor", "Input", "UART", "", ["rainfall sensor", "rain fall sensor", "rain gauge"], "Detects and measures the presence and intensity of rainfall and sends the data over UART.", UART_RR, "", ""),
 ("soil_sensor", "Soil Sensor", "Input", "UART", "", ["soil sensor"], "Measures the moisture in soil and sends the readings over UART, e.g. for irrigation.", UART_RR, "", ""),
 ("thermal_sensor", "Thermal Sensor", "Input", "I2C", "", ["thermal sensor", "contactless temperature sensor"], "Measures the temperature of objects or surroundings without touching them and sends it over I2C.", I2C, "", "Thermal_sensor.py.py"),
 ("voltage_sensor", "Voltage Sensor", "Input", "analog", "", ["voltage sensor"], "Measures the voltage of a source and gives a proportional analog signal that the Raspberry Pi reads through an ADC.", ana(), "", "voltage_sensor.py.py"),
 ("peristaltic_pump", "Peristaltic pump", "Output", "relay", "", ["peristaltic pump"], "Moves or doses liquids in a controlled way. A relay switches the pump ON and OFF so the Raspberry Pi does not drive the motor directly.", [("VCC", "12V (GPIO22 and GPIO23) only, and also to any relay port"), ("GND", "GND")], "", "Relaywaterpump.py.py"),
 ("stepper_motor", "Stepper Motor", "Output", "dedicated", "stepper_motor", ["stepper", "stepper motor"], "Turns in fixed steps for precise position, speed and direction. Control pulses go through a motor driver. It plugs into the stepper motor slot.", [("C1", "1B"), ("C2", "2A"), ("C3", "1A"), ("C4", "2B")], "Stepper motor slot", ""),
]
IFACE = {"digital": "digital", "analog": "analog", "I2C": "I2C", "UART": "UART", "USB": "USB", "relay": "relay", "dedicated": "dedicated", "none": "none"}


def esp32_connection(gw: dict, key: str) -> dict | None:
    dev = gw["devices"].get(key)
    if not dev:
        return None
    pins = [{"pin": p, "connects_to": v} for p, v in dev["pins"].items()] if "pins" in dev else (
        [{"pin": dev["signal"], "connects_to": dev["connect_to"]}] if "signal" in dev else [{"pin": "connection", "connects_to": dev["connect_to"]}])
    return {"kit": ESP, "pins": pins, "warning": dev.get("warning", ""), "note": f"interface: {dev['interface']}",
            "source": {"file": "grovator-esp32-gateway.json", "where": f"devices.{key}"}}


def main() -> None:
    gw = json.loads(GW.read_text(encoding="utf-8"))
    out = KNOWLEDGE_DIR / "sensors"
    for sid, name, cat, typ, gkey, extra, working, pins, note, code in T:
        path = out / f"{sid}.yaml"
        if path.exists() and "generated: true" not in path.read_text(encoding="utf-8"):
            continue
        conns = []
        if pins or note:
            conns.append({"kit": CTRL, "pins": [{"pin": a, "connects_to": b} for a, b in pins], "note": note,
                          "source": {"file": PDF, "where": f"row for {name}"}})
        e = esp32_connection(gw, gkey) if gkey else None
        if e:
            conns.append(e)
        aliases = list(dict.fromkeys([name.lower(), sid.replace("_", " "), *extra]))  # no bare "weight" / "pressure" / "flow": those are ordinary words
        doc = {"id": sid, "name": name, "aliases": [a for a in aliases if len(a) >= 3], "category": cat, "working": working,
               "example_code": code, "gateway_key": gkey, "description": working.split(". ")[0].rstrip(".") + ".",
               "sensor_type": "OUTPUT" if cat == "Output" else "INPUT", "interface": IFACE[typ], "kit_connections": conns,
               "notes": ["generated: true (from the sensor list; edit this file freely, remove this note to protect it from re-import)"],
               "sources": [{"file": PDF, "where": f"row for {name}"}]}
        path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=130), encoding="utf-8")
    print(f"wrote {len(T)} sensor files")


if __name__ == "__main__":
    main()
