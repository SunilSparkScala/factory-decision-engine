import sys
import argparse
import json
from app.agent import FactoryDecisionAgent

def main():
    parser = argparse.ArgumentParser(description="Factory Decision Engine Agent CLI")
    parser.add_argument(
        "prompt",
        nargs="*",
        default=["M17 will be unavailable for 8 hours. Keep high priority deliveries on time while minimizing cost."],
        help="Natural language operational scenario prompt.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of markdown executive briefing.",
    )
    args = parser.parse_args()

    user_prompt = " ".join(args.prompt)
    print("=" * 70)
    print("FACTORY DECISION ENGINE — AGENT ORCHESTRATOR")
    print("=" * 70)
    print(f"Scenario Prompt: {user_prompt}\n")

    agent = FactoryDecisionAgent()
    response = agent.run(user_prompt)

    if args.json:
        print(response.model_dump_json(indent=2))
    else:
        print(response.to_markdown_explanation())

if __name__ == "__main__":
    main()
