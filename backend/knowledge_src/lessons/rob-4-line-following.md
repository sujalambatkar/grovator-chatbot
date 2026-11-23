---
title: "Robotics Lesson 4: Line Following"
category: lessons
order: 24
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Make the robot follow a black line on white paper. This is the Line Follower program.

# Steps
1. Stick a thick black line on white paper.
2. Plug the line sensor into port 3 under the front of the robot.
3. Start with when green flag clicked and a forever loop.
4. Add an if else block that checks if the line sensor sees the line.
5. If it sees the line, move forward. Else, turn slightly until it finds the line again.
6. Run the program with the robot on the line.

# Blocks you will use
line sensor, if else, forever, move forward, turn right degrees.

# Example program
```
when green flag clicked
forever
  if line sensor sees line
    move forward
  else
    turn right 10 degrees
```

# Common mistakes
If the robot loses the line on curves, lower the speed with set speed to 40 percent. A thin or shiny line is hard for the sensor to see.

# Challenge
Add the Wall Stopper check inside the same forever loop so the robot also stops for obstacles.

# Quiz
Question 1: Which sensor follows a line? Answer: the line sensor. Question 2: What should the robot do when it does not see the line? Answer: turn slightly until it finds the line.
