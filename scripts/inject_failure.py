import random

SERVICE_TEST = "src/test/java/com/example/StudentServiceTest.java"

def inject():
    with open(SERVICE_TEST, "r") as f:
        content = f.read()

    code = '''
        java.io.File counter = new java.io.File("flaky.counter");
        int attempt = 1;
        try {
            if (counter.exists()) {
                java.util.Scanner s = new java.util.Scanner(counter);
                attempt = s.nextInt() + 1;
                s.close();
            }
            java.io.FileWriter w = new java.io.FileWriter(counter);
            w.write(String.valueOf(attempt));
            w.close();
        } catch (Exception ignored) {}

        if (attempt == 1) {
            org.junit.jupiter.api.Assertions.fail("Fail on first attempt");
        } else if (attempt == 2) {
            if (Math.random() < 0.5) {
                org.junit.jupiter.api.Assertions.fail("Random fail on second attempt");
            }
        }
        // attempt 3 always passes
'''

    target = "List<Student> result = studentService.getAllStudents();"
    content = content.replace(target, code + target)

    with open(SERVICE_TEST, "w") as f:
        f.write(content)

    print("Injected deterministic flaky pattern")

if __name__ == "__main__":
    inject()
