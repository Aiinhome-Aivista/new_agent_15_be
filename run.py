import os

from app import create_app, db

app = create_app()

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', '3050')),
        debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true',
    )
