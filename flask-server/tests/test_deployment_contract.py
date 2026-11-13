"""Exercise the same offline capability gate used on the production image."""
from deployment_contract import validate

def test_exact_image_authentication_and_upgrade_contract():
    validate()
