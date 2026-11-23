---
title: "Sample Block Programs"
category: blocks
order: 38
role_visibility: [student, teacher, admin, superadmin]
---
# Blink LED (IoT)
when green flag clicked, forever, set LED on, wait 1 seconds, set LED off, wait 1 seconds. The LED blinks once a second. Try it in IoT Lesson 3.

# Temperature Alarm (IoT Lesson 3)
when green flag clicked, forever, if temperature value is greater than 30 then set LED on and buzzer beep for 1 seconds, else set LED off.

# Smart Night Light (IoT)
when green flag clicked, forever, if light level is less than 20 then set LED on, else set LED off. The LED turns on by itself when the room gets dark.

# Square Dance (Robotics Lesson 2)
when green flag clicked, repeat 4, move forward for 1 seconds, turn right 90 degrees. The robot drives in a square.

# Wall Stopper (Robotics Lesson 3)
when green flag clicked, forever, if distance is less than 10 then stop all motors, else move forward.

# Line Follower (Robotics Lesson 4)
when green flag clicked, forever, if line sensor sees line then move forward, else turn right 10 degrees.

# Countdown Timer
when green flag clicked, set seconds to 10, repeat 10, display text seconds, wait 1 seconds, change seconds by -1. At the end add buzzer beep for 2 seconds.

# Maze Runner (Robotics final challenge)
Combine the Wall Stopper and Line Follower inside one forever loop. Add a variable called turns that goes up each time the robot turns.
