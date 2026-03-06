"""
Fixture: sample Flask app for extractor tests.
Not meant to be run — only parsed by python_ast extractor.
"""
from flask import Flask

app = Flask(__name__)


@app.route("/api/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    """Get a single item."""
    pass


@app.route("/api/items", methods=["GET", "POST"])
def items():
    """List or create items."""
    pass


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    """Delete an item."""
    pass
