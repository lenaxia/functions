def handler(event):
    """
    Hello World handler for Fission

    Args:
        event: Dictionary containing request data

    Returns:
        Dictionary with response data
    """
    name = event.get("name", "World")
    return {"message": f"Hello, {name}!", "status": "success"}
