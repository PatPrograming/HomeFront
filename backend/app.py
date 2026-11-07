from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.get("/api/home")
def home():
    return jsonify(
        {
            "mission": ["Mission", "statement."],
            "buttons": ["button1", "button2"],
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
