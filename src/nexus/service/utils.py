def convert_lists_to_tuples(obj):
    if isinstance(obj, list):
        return tuple(convert_lists_to_tuples(item) for item in obj)
    elif isinstance(obj, dict):
        return {key: convert_lists_to_tuples(value) for key, value in obj.items()}
    return obj
