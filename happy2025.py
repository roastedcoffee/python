import time
import os
import shutil

def clear_console():
    """Clears the console for animation effect."""
    os.system('cls' if os.name == 'nt' else 'clear')

def fireworks_display():
    """Displays a fireworks animation with a message."""
    fireworks = ["🎆", "✨", "🧨"]
    message = "🎉 Happy 2025! 🎉"
    
    # Get console dimensions
    columns, rows = shutil.get_terminal_size(fallback=(80, 24))
    center_position = columns // 2  # Center alignment

    # Fireworks animation
    for _ in range(10):
        clear_console()
        print("\n" * (rows // 3))  # Push the animation vertically to the middle
        for firework in fireworks:
            print(" " * (center_position - 2) + firework + message)  # Center the fireworks
        time.sleep(0.5)
    
    # Display the final message
    clear_console()
    print("\n" * (rows // 3))  # Push the message vertically to the middle
    print(message.center(columns))  # Center the message horizontally
    print("\n")

if __name__ == "__main__":
    fireworks_display()
