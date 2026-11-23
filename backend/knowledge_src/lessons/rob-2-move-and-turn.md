---
title: "Robotics Lesson 2: Move and Turn"
category: lessons
order: 22
role_visibility: [student, teacher, admin, superadmin]
---
# Goal
Use move and turn blocks with a repeat loop to drive the robot in a square.

# Steps
1. Start with when green flag clicked.
2. Add repeat 4 times.
3. Inside it, add move forward for 1 seconds and turn right 90 degrees.
4. Press Run. The robot draws a square on the floor.
5. Change the 4 and the 90 to make other shapes. A triangle needs repeat 3 and a 120 degree turn.

# Blocks you will use
move forward for seconds, turn right degrees, turn left degrees, repeat.

# Example program
```
when green flag clicked
repeat 4
  move forward for 1 seconds
  turn right 90 degrees
```

# Common mistakes
If the square does not close, the robot is drifting: check both wheels are tight and try a lower speed with set speed.

# Challenge
Make the robot dance: repeat a pattern of forward, turn left, backward, turn right.

# Quiz
Question 1: How many degrees do you turn for a square? Answer: 90. Question 2: Which block repeats instructions? Answer: repeat.
