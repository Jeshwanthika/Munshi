"""Talk to Munshi in the terminal.  Run:  python chat.py   (type 'exit' to quit)"""
from agent import chat


def show_tool(name, args):
    print(f"   [Munshi is using PayPal: {name}]")


def main():
    print("Munshi - your AI accountant. Try: 'Bill Acme Corp $1200 for a landing page'\n")
    history = []
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        try:
            print("\nMunshi:", chat(history, text, on_tool=show_tool), "\n")
        except Exception as e:
            print(f"\n[error] {e}\n")


if __name__ == "__main__":
    main()
