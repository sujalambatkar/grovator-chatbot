---
title: "Robotics Lesson 3: Distance Sensor"
category: lessons
order: 23
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Use the distance sensor so the robot stops before it hits a wall. This is the Wall Stopper program.

# Steps
1. Plug the distance sensor into port 2 at the front of the robot.
2. Start with when green flag clicked and a forever loop.
3. Add an if else block.
4. In the condition, check if distance is less than 10.
5. If true, add stop all motors. Else, add move forward.
6. Run it facing a wall and watch it stop about 10 centimetres away.

# Blocks you will use
distance, if else, less than, forever, move forward, stop all motors.

# Example program
```
when green flag clicked
forever
  if distance < 10
    stop all motors
  else
    move forward
```

# Common mistakes
If the robot crashes, the number 10 may be too small for the speed: try 20. The distance sensor measures in centimetres.

# Quiz
Question 1: What unit does the distance sensor use? Answer: centimetres. Question 2: Which block stops the robot? Answer: stop all motors.
