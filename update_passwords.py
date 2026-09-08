from app import create_app, db
from app.models.user import User
import bcrypt

app = create_app()

with app.app_context():
    # Generate a valid bcrypt hash for 'Devaa@2024'
    valid_hash = bcrypt.hashpw('Devaa@2024'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    # Update all users to have this valid hash
    users = User.query.all()
    for user in users:
        user.password_hash = valid_hash
        print(f"Updated password for {user.email}")
        
    db.session.commit()
    print("All passwords updated successfully!")
