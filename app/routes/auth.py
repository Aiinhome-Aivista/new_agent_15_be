from flask import Blueprint, request, jsonify
from app.models.user import User
from app.models.role import Role
from app.utils.auth import verify_password, generate_token, require_auth

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({"error": "Missing email or password"}), 400
        
    user = User.query.filter_by(email=data['email']).first()
    if not user or not user.is_active:
        return jsonify({"error": "Invalid credentials"}), 401
        
    if not verify_password(data['password'], user.password_hash):
        return jsonify({"error": "Invalid credentials"}), 401
        
    role = Role.query.get(user.role_id)
    role_name = role.name if role else "User"
    
    token = generate_token(user.id, role_name)
    
    return jsonify({
        "token": token,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": role_name
        }
    }), 200

@auth_bp.route('/me', methods=['GET'])
@require_auth
def me():
    user = request.current_user
    role = Role.query.get(user.role_id)
    return jsonify({
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": role.name if role else "User"
        }
    }), 200

@auth_bp.route('/logout', methods=['POST'])
@require_auth
def logout():
    # Since we are using stateless JWT, logout is primarily handled on the frontend
    # by deleting the token. We could implement a token blacklist here if needed.
    return jsonify({"message": "Logged out successfully"}), 200
