---
title: "IoT Lesson 4: Send Data to the Dashboard"
category: lessons
order: 14
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Send the temperature to the Govator dashboard and read the live graph.

# Steps
1. Make sure the kit is connected and the temperature sensor is in port 1.
2. Start with when green flag clicked and a forever loop.
3. Add send data with the name temperature and the value from the temperature value block.
4. Add wait 5 seconds so you send a reading every five seconds.
5. Run the program and open My Dashboard to see the live graph. Your teacher can see the same graph in the class view.

# Blocks you will use
send data, temperature value, forever, wait seconds.

# Example program
```
when green flag clicked
forever
  send data (temperature, temperature value)
  wait 5 seconds
```

# Common mistakes
No graph usually means the kit is disconnected or the data name is spelled differently in two places. Sending too fast is not allowed: use at least 1 second between readings.

# Quiz
Question 1: Where can you see your data? Answer: on My Dashboard. Question 2: Who else can see the graph? Answer: your teacher in the class view.
