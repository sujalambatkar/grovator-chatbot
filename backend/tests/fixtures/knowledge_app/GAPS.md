# GAPS: unknowns and conflicts to resolve

The chatbot treats every item here as "not documented yet" or as an open conflict. When you resolve one, edit the YAML
files named in the item and re-run `python -m knowledge.validate && python -m ingest.run`. Then delete the item here.

## A. Conflicts between sources

1. **I2C or UART for the pulse oximeter?** (`sensors/pulseoximeter_max30102.yaml`, `sensors/pulseoximeter_v2_uart.yaml`)
   - `Max30102_Blocks_Requirement.pdf` has I2C code (SCL=22, SDA=21, address 0x57) and the lesson circuit is wired to
     SDA/SCL, but the same PDF's "Widget requirement" lists pins 3V3, GND, RX, TX (UART style).
   - `Pulseoximeter_V2.0.pdf` (not in your brief) has UART code (UART1, 9600 baud, TX=GPIO17, RX=GPIO16) and the same
     3V3/GND/RX/TX widget. It reads as if the widget section in the MAX30102 PDF was copied from this V2.0 document.
   - **What I did:** two sensors. `pulseoximeter_max30102` = I2C (lesson + MAX30102 blocks). `pulseoximeter_v2_uart` = UART
     (V2.0 PDF). The bot gives I2C pins for the lesson and never RX/TX for it.
   - **Please confirm:** (a) is the widget pin list in the MAX30102 PDF a copy-paste error? (b) are these two really
     different sensors or one product with two versions? Both PDFs use the title "Use [Pulseoximeter(Max30102)]" and
     device type `pulseoximeterV2.0` / `pulseoximeter_v2.0`.
2. **Sensor version / variable names.** The lesson block program says "Use Pulseoximeter **V3** (Max30102)" with variables
   `pulseoximeterv3_bpm_1`. The block spec says `pulseoximeterV2_n`, `pulseoximeterV2_n_heartrate`,
   `pulseoximeterV2_n_oximeter`. The V2.0 UART PDF says `pulseoximeterV2_heartrate_n`, `pulseoximeterV2_oximeter_n`
   (different order). Which naming is final?
3. **Two block sets for the same sensor.** The lesson uses blocks "Use Pulseoximeter V3 (Max30102)", "Get Heartrate",
   "Get spO2" with the `max30102` library (`get_values()`). The block spec uses raw-register blocks "Use", "Read
   PulseOximeter Data", "Store Heart rate and Oximeter Data". Return order differs: library `get_values()` is [bpm, spo2],
   the block spec `get_heart_rate_spo2()` is (bpm, spo2), but the UART `read_spo2_hr()` returns (spo2, heart_rate).
4. **Lesson text vs lesson code.** The challenge says the LEDs show the *oxygen* level (one, two or three lights). The
   program lights the LEDs by *heart rate* (>=90, 80-89, <80); SpO2 only prints "Normal/Low oxygen level". The bot follows
   the code.
5. **The "LED 3 OFF" typo.** The program prints "Heart rate: Low - LED 3 OFF" but runs `led_3.on()`. The bot follows the
   code (LED 3 turns ON) and explains the typo if asked.
6. **Sensor description.** The "Decoding" slide shows a board labelled MAX30100 and describes an analog pulse sensor with an
   amplifier and "+5V logic level" output, which does not match an I2C MAX30102 (3.3V). The bot explains the principle
   (red LED + light detector) but does not repeat the "+5V" claim as a fact about this sensor.
7. **LED GPIOs vs the circuit diagram.** The program uses GPIO27, GPIO23, GPIO13 for the LEDs. In the circuit diagram the LED
   board's wires go to the kit's lower terminal, whose labels read like GPIO32 / GPIO23 / GPIO2 (hard to read). Confirm.

8. **Lesson LED GPIOs vs the gateway dataset.** The lesson drives its LEDs on GPIO27, GPIO23, GPIO13, but the gateway dataset's digital connector pins are GPIO5, 4, 19, 25, 26, 18, 32 (GPIO23, 27 and 13 are not in that list). Which is right for the ESP32 kit?
9. **Pulse oximeter chip.** The sensor list says MAX30100; the lesson and the block spec say MAX30102 (the lesson slide image is also labelled MAX30100). The sensor list gives VCC "3.3 or 5V"; the lesson wires VIN to 3.3V.
10. **Vibration sensor.** The sensor list says analog (AI1 to AI4); the ESP32 gateway dataset says digital D0. The bot shows both, per kit.
11. **Hall effect sensor.** The sensor list's text says the output is HIGH when a magnet is detected in one sentence and LOW in the printed table: not stated reliably.
12. **Same kit?** The bot treats the lesson's "ESP32 kit" and the dataset's "Grovator ESP32 IoT Gateway" as one product (same I2C pins: SDA GPIO21, SCL GPIO22). Please confirm.
13. **UART crossing.** The sensor list writes "Rx Tx / Tx Rx" for most UART sensors (read as crossed: sensor Rx to controller Tx) but "RX to RX, TX to TX" for the NPK, rainfall and soil sensors (stated as is; the NPK row says this is intentional only for NPK). Please confirm the other two.
14. **Relay module.** The "Right Side" / "Left Side" connection text is only partly clear (a second VCC 12V / GND pair appears); the bot repeats it as written.
15. **Relay pin names.** The sensor list says relay = GPIO22 and GPIO23; the controller photo labels Relay 1 = GPIO23 and Relay 2 = GPIO22.

16. **Two sensor lists disagree.** The controller list ("RPI", 50 sensors) and the ESP list (47 sensors) differ for: Sound Sensor (controller digital D0; ESP analog GPIO32/36; gateway dataset digital D0), Vibration Sensor (ESP: analog GPIO32/36 but its text also describes a digital HIGH above a threshold; controller analog AI1-4; gateway dataset digital D0), Rain Fall Sensor (controller UART RX to RX; ESP I2C SDA/SCL), Soil Sensor (controller RX to RX; ESP RX to TX), RTC (controller: no pins; ESP: I2C SDA/SCL), Camera (gateway dataset and controller: USB; ESP list: "ESP Camera" over UART). The bot shows each kit's own row and says which kit.
17. **ESP list quirks.** RFID Sensor: text says the data goes "through an interface such as SPI" but the pins are UART. Wind Speed Sensor: text says pulses go to a digital input pin but the pins are analog GPIO32/36. Several rows say "Raspberry Pi" (copied from the controller list). DC Motor, Water Pump and Relay have no pin list on the ESP list (only a tiny relay diagram labelled "ESP-32 Kit Side Connection": GND, 12V, GND, GPIO, 5V: too small to use). "TDC Sensor" in the details column is read as TDS. The ESP list has only PH Sensor V2 (no V1), and 8 sensors the controller list lacks (Sound Sensor Analog, ESP Camera, Current, RFID, Spectral, Wind Direction, Wind Speed, BME).
18. **Controller list sensors the ESP list lacks** (the bot says the ESP32 connection is not documented): Accelerometer, Blood Pressure, GPS, Particulate Matter, PH Sensor (V1), Peristaltic pump, Pressure, Power-related rows other than Power Meter, Stepper Motor wiring (C1 to 1B ... is controller-only; the ESP list only says "Stepper Motor - Stepper Motor" slot), Weight and others already listed under B3c.
19. **Example programs.** Both lists name example files (e.g. "Alcohol_sensor.py.py" for the controller, "Alcohol Sensor.py" for the ESP); the SVG diagrams and the code links ("SVG Drive Link", file links) are links to documents I cannot open, so only the file names are known.

## B. Not documented (the bot says "I don't have that detail yet")

1. **Pulse oximeter on the Grovator controller: partly resolved.** The sensor list ("RPI" controller) says: VCC to 3.3 or 5V, GND to Ground, SDA to any SDA pin, SCL to any SCL pin. The bot gives exactly that. Still not documented: a GPIO number for them (the ESP32's 21/22 do NOT apply: the controller's GPIO numbers are Raspberry Pi numbers, e.g. GPIO22 is a relay), and which of the three photographed [GND, 3.3V, SDA, SCL] terminal groups to use ("any").
2. **LED board wiring in the lesson.** Pin labels (VCC, DO, DO, DO, GND) and the kit terminals are not legible in the diagram. (The sensor list gives the generic LED Module wiring: VCC, GND, one D0 to any digital GPIO; the bot uses that for "how do I connect the LED module".)
3. **Full ESP32 kit pin map**, other than the interfaces in the gateway dataset (digital, analog, I2C, UART, keypad, motors).
3b. **Example programs.** The sensor list names an example file per sensor (e.g. Alcohol_sensor.py.py) but the code is not in the documents; SVG links are not included either.
3c. **No ESP32 entry** in the gateway dataset for 28 sensors of the sensor list (e.g. GPS, pH, ECG): the bot says the ESP32 connection is not documented.
3d. **RTC and Camera** have no pin list in the sensor list (RTC: none; Camera: USB port).
4. **Which kit terminal carries TXD/RXD** for the UART V2.0 sensor, and whether GPIO16/GPIO17 are those terminals.
5. **Block definitions for the lesson's other blocks** (Use Led Board, Use Time, Set LED at Pin, Turn On/Off LED, print,
   Pause, if/else, repeat while true, Get Heartrate / Get spO2 of the V3 library). Only the three sensor blocks and the four
   UART blocks have code.
6. **Troubleshooting beyond the code.** The documents give no troubleshooting list. FAQ answers are derived from what the
   code does (no finger gives 0,0 and "Place Your Finger on Sensor"). Please add real classroom tips if you have them.
7. **Expected output.** The lesson shows no sample serial output. The bot can list the messages the program prints, but not
   typical values.
8. **Sensor operating voltage / power.** The lesson wires VIN to the kit's 3.3V; no other voltage is documented.

## C. Quality issues in the sources (from reading the code)

1. **SpO2 can exceed 100.** `spo2 = round(min(100, max(85, spo2)), 0)+4` adds 4 *after* clamping to 100, so it can output
   up to 104. The calculation is a rough classroom approximation, not clinical. The bot never calls readings accurate.
2. **PDF indentation.** The code in `Max30102_Blocks_Requirement.pdf` changes indentation at the page break (inside the
   `while` loop). It was normalised to 4 spaces so it is valid Python. The slide text of the lesson program lost its
   indentation and was rebuilt from the block picture on slide 11 (three separate if/else blocks inside `if bpm != 0`).
3. **Block title.** "Store Heart rate and Oximeter Data" comes from a handwritten sketch. Confirm the final title and
   variable names (`bpm_data_1`, `spo2_data_1` are called "underdefined" in the PDF).
4. **Typos in the lesson.** "Heartrate :" (no space), "Place Your Finger  on Sensor" (double space in the slide text).

## D. Unverified media

`media/controller_photo_*.yaml` are `verified: false`: the labels were transcribed from photos (I re-read all four and
found no difference from your transcription, but a person must confirm). Lesson slide images (`deck_page_*`) are real
document pages and are marked verified.
