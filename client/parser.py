from show_message import show_message

def parse_command(data):
    match data["type"]:
        case "show_message":
            show_message(
                data.get("title"),
                data.get("body")
            )
        case _:
            print(f"Unknown command type: {data.get('type')}")
