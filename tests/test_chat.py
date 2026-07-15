from chat_tutor import TutorSession

session = TutorSession(
    "sample.png",
    mode="student"
)

print("\n=== INITIAL EXPLANATION ===\n")
print(session.explanation)

while True:

    question = input("\nAsk a question (or type exit): ")

    if question.lower() == "exit":
        break

    answer = session.ask(question)

    print("\n=== CLICKTUTOR ===\n")
    print(answer)
