---
title: "IoT Lesson 3: LED and Buzzer"
category: lessons
order: 13
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Control the LED and buzzer and use a condition to build a Temperature Alarm.

# Parts needed
Controller board, temperature sensor on port 1, LED on port 3, buzzer on port 4.

# Steps
1. Plug the LED into port 3 and the buzzer into port 4.
2. Test each part with set LED on and buzzer beep for 1 seconds.
3. Build the Temperature Alarm: forever, then if temperature value is greater than 30, set LED on and buzzer beep, else set LED off.
4. Run the program and warm the sensor to trigger the alarm.

# Blocks you will use
set LED on, set LED off, buzzer beep for seconds, if else, temperature value, greater than.

# Common mistakes
If the LED does not light, check it is plugged in the right way round: the longer leg goes to the plus side. An alarm that never stops usually has the else part missing.

# Challenge
Add a variable called alarms that goes up by 1 every time the alarm sounds.

# Quiz
Question 1: Which block lets the program choose between two actions? Answer: if else. Question 2: What does the buzzer beep block need? Answer: how many seconds to beep.

# Previous and next lessons
Previous lesson: IoT Lesson 2: Temperature Sensor.
Next lesson: IoT Lesson 4: Send Data to the Dashboard. First you do the Smart Night Light activity.
