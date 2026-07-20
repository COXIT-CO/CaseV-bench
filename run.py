from app import create_app
from app.config import DEBUG

flask_app = create_app()

if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8000, debug=DEBUG)
