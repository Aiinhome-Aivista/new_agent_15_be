from flask import jsonify

def api_response(status_code: int, is_success: bool, message: str, data=None):
    """
    Standardize API responses across the backend application.
    """
    response_body = {
        "status_code": status_code,
        "is_success": is_success,
        "message": message,
        "data": data if data is not None else {}
    }
    return jsonify(response_body), status_code
