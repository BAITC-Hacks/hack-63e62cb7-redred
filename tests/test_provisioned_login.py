import unittest

from pydantic import ValidationError

from backend.auth import Credentials, Registration, _email, _hash_password, _verify_password
from backend.errors import APIError


class ProvisionedLoginTest(unittest.TestCase):
    def test_provisioned_login_accepts_short_password(self):
        value = Credentials(email="client", password="client")
        hashed = _hash_password(value.password)
        self.assertTrue(_verify_password("client", hashed))
        self.assertFalse(_verify_password("wrong", hashed))

    def test_registration_keeps_minimum_password_length(self):
        with self.assertRaises(ValidationError):
            Registration(email="client@example.com", password="client")

    def test_registration_still_requires_email(self):
        with self.assertRaises(APIError):
            _email("client")

    def test_login_rejects_empty_password(self):
        with self.assertRaises(ValidationError):
            Credentials(email="client", password="")
