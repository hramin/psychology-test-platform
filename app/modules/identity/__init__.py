"""Identity module: users, organizations, and phone/OTP authentication.

Owns the ``users`` / ``organizations`` / ``org_members`` tables. Other modules
read identity only through ``identity.service`` — never these tables directly.
"""
