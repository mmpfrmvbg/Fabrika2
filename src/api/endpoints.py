"""API endpoints module.

Contains health check, info, and other API endpoints for Forge API.
"""

from flask import Blueprint, jsonify

api = Blueprint('api', __name__)


@api.route('/health', methods=['GET'])
def health_check():
    """Базовый эндпоинт проверки здоровья."""
    try:
        return jsonify({'status': 'ok', 'message': 'Service is running'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@api.route('/info', methods=['GET'])
def get_info():
    """Эндпоинт информации о сервисе."""
    try:
        return jsonify({
            'service': 'Forge API',
            'version': '1.0.0',
            'status': 'active'
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@api.route('/api/v1/status', methods=['GET'])
def get_status():
    """Эндпоинт статуса API."""
    try:
        return jsonify({
            'api': 'Forge API',
            'endpoint': '/api/v1/status',
            'ready': True
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
