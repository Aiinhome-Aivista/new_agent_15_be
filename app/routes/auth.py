from flask import Blueprint, request, jsonify
from app.utils.responses import api_response
from app.models.user import User
from app.models.role import Role
from app.utils.auth import verify_password, generate_token, require_auth

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return api_response(400, False, "Missing email or password")
        
    user = User.query.filter_by(email=data['email']).first()
    if not user or not user.is_active:
        return api_response(401, False, "Invalid credentials")
        
    if not verify_password(data['password'], user.password_hash):
        return api_response(401, False, "Invalid credentials")
        
    role = Role.query.get(user.role_id)
    role_name = role.name if role else "User"
    
    token = generate_token(user.id, role_name)
    
    return api_response(200, True, "Success", {
        "token": token,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": role_name
        }
    })

@auth_bp.route('/me', methods=['GET'])
@require_auth
def me():
    user = request.current_user
    role = Role.query.get(user.role_id)
    return api_response(200, True, "Success", {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": role.name if role else "User"
        }
    })

@auth_bp.route('/logout', methods=['POST'])
def logout():
    # Since we are using stateless JWT, logout is primarily handled on the frontend
    # by deleting the token. We respond immediately without blocking on remote DB.
    return api_response(200, True, "Success", {"message": "Logged out successfully"})
