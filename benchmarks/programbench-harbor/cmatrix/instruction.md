You are reverse-engineering a compiled binary at /workspace/executable.

The binary has EXECUTE-ONLY permissions (mode 111). You CANNOT read its contents. You can only run it.

Your goal: write source code and a compile.sh script that produces a behaviorally-equivalent executable at /workspace/executable.

Strategy:
1. Run the executable with various arguments to discover its behavior (--help, -h, no args, etc.)
2. Create test inputs and capture exact outputs
3. Read any documentation in /workspace/
4. Write source code matching the observed behavior
5. Create compile.sh that builds the executable
6. Test your implementation against the original using differential testing

Back up the original first: cp /workspace/executable /workspace/executable.bak
Your compile.sh must produce the executable at /workspace/executable.
The evaluation compares your output against the original on hidden test cases.
