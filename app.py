from flask import Flask, request, abort
import os
from main import run

app = Flask(__name__)

@app.route("/run")
def trigger():
    token = request.args.get("token")
    if token != os.environ.get("RUN_TOKEN"):
        abort(403)
    run()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
