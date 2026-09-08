import jwt
import bcrypt
import datetime
from functools import wraps
from flask import request, jsonify, current_app
from app.models.user import User
from app.models.role import Role

def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def hash_password(plain_password):
    return bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def generate_token(user_id, role_name):
    payload = {
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=1),
        'iat': datetime.datetime.utcnow(),
        'sub': str(user_id),
        'role': role_name
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

def decode_token(token):
    try:
        payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return 'Signature expired. Please log in again.'
    except jwt.InvalidTokenError:
        return 'Invalid token. Please log in again.'

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            print("AUTH ERROR: Missing Authorization Header")
            return jsonify({"error": "Missing Authorization Header"}), 401
        
        try:
            token = auth_header.split(" ")[1]
        except IndexError:
            print("AUTH ERROR: Bearer token malformed")
            return jsonify({"error": "Bearer token malformed"}), 401
            
        payload = decode_token(token)
        if isinstance(payload, str):
            print(f"AUTH ERROR: Token decoding failed: {payload}")
            return jsonify({"error": payload}), 401
            
        user = User.query.get(payload['sub'])
        if not user or not user.is_active:
            print(f"AUTH ERROR: User not found or inactive. User ID: {payload.get('sub')}")
            return jsonify({"error": "User not found or inactive"}), 401
            
        request.current_user = user
        return f(*args, **kwargs)
    return decorated

def require_role(roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(request, 'current_user'):
                return jsonify({"error": "Authentication required"}), 401
                
            user_role = Role.query.get(request.current_user.role_id)
            if not user_role or user_role.name not in roles:
                return jsonify({"error": "Insufficient permissions"}), 403
                
            return f(*args, **kwargs)
        return decorated
    return decorator
