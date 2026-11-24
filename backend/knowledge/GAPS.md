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

## B. Not documented (the bot says "I don't have that detail yet")

1. **How does the pulse oximeter connect to the Grovator controller?** (`kits/govator_controller_pi.yaml`) Its GPIO
   numbers do NOT match the lesson's ESP32 pins (GPIO22 is Relay 2 there, but I2C SCL in the lesson; GPIO21, GPIO23, GPIO27
   are plain GPIO terminals). No mapping was created. It does have three I2C groups [GND, 3.3V, SDA, SCL], but whether the
   sensor uses them is not stated in any document.
2. **LED board wiring.** Pin labels (VCC, DO, DO, DO, GND) and the kit terminals are not legible in the diagram.
3. **Full ESP32 kit pin map**, other than I2C and the three lesson LED GPIOs.
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
