import os
import time

def main():
    print(f"noop.py Process ID: {os.getpid()}, User ID: {os.getuid()}")

    # Wait indefinitely
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
