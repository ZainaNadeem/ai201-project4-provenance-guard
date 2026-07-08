"""Run the Provenance Guard API server: python run.py"""

from provenance_guard.app import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
