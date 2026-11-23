---
title: "IoT Lesson 2: Temperature Sensor"
category: lessons
order: 12
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Read the temperature sensor and show the value on the display.

# Parts needed
Controller board, temperature sensor on port 1, small display.

# Steps
1. Plug the temperature sensor into port 1 and check the port number in your blocks matches.
2. Start with when green flag clicked, then add a forever loop.
3. Inside the loop, add display text and drop the temperature value block into it.
4. Add wait 1 seconds so the number is easy to read.
5. Press Run, then warm the sensor with your hand and watch the number rise.

# Blocks you will use
temperature value, display text, forever, wait seconds.

# Example program
```
when green flag clicked
forever
  display text (temperature value)
  wait 1 seconds
```

# Common mistakes
If the number never changes, the sensor is probably in the wrong port. The temperature is in degrees Celsius.

# Challenge
Make the display say Hot when the temperature is above 30. You will need an if block.

# Quiz
Question 1: Which block gives the temperature? Answer: temperature value. Question 2: Why add wait 1 seconds? Answer: so the display does not change too fast to read.
