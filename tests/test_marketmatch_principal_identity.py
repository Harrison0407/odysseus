"""Synthetic tests for the pure MarketMatch principal identity contract."""

from __future__ import annotations

import ast
import contextlib
from dataclasses import FrozenInstanceError, fields
import io
from pathlib import Path
import subprocess
import traceback
import unittest

import src.marketmatch_principal_identity as principal_module
from src.marketmatch_principal_identity import (
    MAX_ALIAS_DECLARATIONS,
    MAX_ALIAS_UTF8_BYTES,
    PRINCIPAL_ID_PREFIX,
    PRINCIPAL_ID_SUFFIX_LENGTH,
    PrincipalAliasDeclaration,
    PrincipalAliasType,
    PrincipalIdentity,
    PrincipalIdentityCode,
    PrincipalIdentityError,
    PrincipalStatus,
    create_principal_alias_declaration,
    create_principal_identity,
    resolve_principal_alias,
    transition_principal_status,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_principal_identity.py"
APPROVED_PATHS = {
    "src/marketmatch_principal_identity.py",
    "tests/test_marketmatch_principal_identity.py",
}

PRINCIPAL_A = PRINCIPAL_ID_PREFIX + "a" * PRINCIPAL_ID_SUFFIX_LENGTH
PRINCIPAL_B = PRINCIPAL_ID_PREFIX + "b" * PRINCIPAL_ID_SUFFIX_LENGTH


def _identity(
    principal_id: object = PRINCIPAL_A,
    status: object = PrincipalStatus.ACTIVE,
) -> PrincipalIdentity:
    return create_principal_identity(principal_id=principal_id, status=status)


def _alias(
    *,
    identity: object | None = None,
    alias: object = "alice",
    alias_type: object = PrincipalAliasType.USERNAME,
) -> PrincipalAliasDeclaration:
    if identity is None:
        identity = _identity()
    return create_principal_alias_declaration(
        identity,
        alias=alias,
        alias_type=alias_type,
    )


def _assert_code(
    test: unittest.TestCase,
    code: PrincipalIdentityCode,
    callable_object,
    *args: object,
    **kwargs: object,
) -> PrincipalIdentityError:
    with test.assertRaises(PrincipalIdentityError) as caught:
        callable_object(*args, **kwargs)
    test.assertIs(caught.exception.code, code)
    test.assertEqual(str(caught.exception), code.value)
    return caught.exception


class PrincipalConstructionTests(unittest.TestCase):
    def test_valid_active_principal(self) -> None:
        result = _identity()
        self.assertEqual(result.principal_id, PRINCIPAL_A)
        self.assertIs(result.status, PrincipalStatus.ACTIVE)

    def test_valid_disabled_principal(self) -> None:
        self.assertIs(
            _identity(status=PrincipalStatus.DISABLED).status,
            PrincipalStatus.DISABLED,
        )

    def test_valid_deleted_principal(self) -> None:
        self.assertIs(
            _identity(status=PrincipalStatus.DELETED).status,
            PrincipalStatus.DELETED,
        )

    def test_exact_principal_grammar(self) -> None:
        value = PRINCIPAL_ID_PREFIX + "a1" * 13
        self.assertEqual(_identity(value).principal_id, value)

    def test_empty_principal_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            "",
        )

    def test_wrong_prefix_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            "principal-" + "a" * 26,
        )

    def test_uppercase_principal_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            PRINCIPAL_ID_PREFIX + "A" * 26,
        )

    def test_short_principal_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            PRINCIPAL_ID_PREFIX + "a" * 25,
        )

    def test_long_principal_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            PRINCIPAL_ID_PREFIX + "a" * 27,
        )

    def test_unicode_principal_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            PRINCIPAL_ID_PREFIX + "a" * 25 + "é",
        )

    def test_path_and_whitespace_principals_rejected(self) -> None:
        for suffix in ("a/", "a\\", "a.", "a ", "a\n"):
            with self.subTest(suffix=suffix):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
                    _identity,
                    PRINCIPAL_ID_PREFIX + "a" * 24 + suffix,
                )

    def test_semantic_principals_rejected(self) -> None:
        for value in (
            "alice@example.com",
            "/home/alice",
            "https://example.com",
            "www.example.com",
            "127.0.0.1",
            "internal-tool",
            "auth-disabled",
        ):
            with self.subTest(value=value):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
                    _identity,
                    value,
                )

    def test_non_string_principal_rejected(self) -> None:
        for value in (None, 7, b"principal", True):
            with self.subTest(value=type(value).__name__):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
                    _identity,
                    value,
                )

    def test_string_subclass_principal_rejected_without_protocol_use(self) -> None:
        class HostileString(str):
            def isascii(self) -> bool:
                raise AssertionError("must not run")

        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            HostileString(PRINCIPAL_A),
        )

    def test_status_requires_exact_enum(self) -> None:
        for value in ("active", None, 1):
            with self.subTest(value=value):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_PRINCIPAL_STATUS,
                    _identity,
                    PRINCIPAL_A,
                    value,
                )

    def test_direct_identity_construction_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT,
            PrincipalIdentity,
        )

    def test_identity_exact_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(PrincipalIdentity)),
            ("principal_id", "status"),
        )

    def test_identity_is_frozen_and_slotted(self) -> None:
        result = _identity()
        with self.assertRaises(FrozenInstanceError):
            result.status = PrincipalStatus.DELETED  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))

    def test_identity_repr_is_fixed_and_non_identifying(self) -> None:
        rendered = repr(_identity())
        self.assertEqual(rendered, "PrincipalIdentity()")
        self.assertNotIn(PRINCIPAL_A, rendered)
        self.assertNotIn("active", rendered)


class PrincipalTransitionTests(unittest.TestCase):
    def test_active_to_disabled(self) -> None:
        result = transition_principal_status(
            _identity(), next_status=PrincipalStatus.DISABLED
        )
        self.assertIs(result.status, PrincipalStatus.DISABLED)

    def test_active_to_deleted(self) -> None:
        result = transition_principal_status(
            _identity(), next_status=PrincipalStatus.DELETED
        )
        self.assertIs(result.status, PrincipalStatus.DELETED)

    def test_disabled_to_active(self) -> None:
        result = transition_principal_status(
            _identity(status=PrincipalStatus.DISABLED),
            next_status=PrincipalStatus.ACTIVE,
        )
        self.assertIs(result.status, PrincipalStatus.ACTIVE)

    def test_disabled_to_deleted(self) -> None:
        result = transition_principal_status(
            _identity(status=PrincipalStatus.DISABLED),
            next_status=PrincipalStatus.DELETED,
        )
        self.assertIs(result.status, PrincipalStatus.DELETED)

    def test_deleted_is_terminal(self) -> None:
        for target in PrincipalStatus:
            with self.subTest(target=target):
                _assert_code(
                    self,
                    PrincipalIdentityCode.TERMINAL_PRINCIPAL,
                    transition_principal_status,
                    _identity(status=PrincipalStatus.DELETED),
                    next_status=target,
                )

    def test_same_active_state_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_STATUS_TRANSITION,
            transition_principal_status,
            _identity(),
            next_status=PrincipalStatus.ACTIVE,
        )

    def test_same_disabled_state_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_STATUS_TRANSITION,
            transition_principal_status,
            _identity(status=PrincipalStatus.DISABLED),
            next_status=PrincipalStatus.DISABLED,
        )

    def test_invalid_next_status_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_STATUS,
            transition_principal_status,
            _identity(),
            next_status="disabled",
        )

    def test_principal_id_preserved_and_new_object_returned(self) -> None:
        original = _identity()
        result = transition_principal_status(
            original, next_status=PrincipalStatus.DISABLED
        )
        self.assertEqual(result.principal_id, original.principal_id)
        self.assertIsNot(result, original)

    def test_original_identity_unchanged(self) -> None:
        original = _identity()
        transition_principal_status(original, next_status=PrincipalStatus.DISABLED)
        self.assertIs(original.status, PrincipalStatus.ACTIVE)

    def test_forged_identity_rejected(self) -> None:
        forged = object.__new__(PrincipalIdentity)
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT,
            transition_principal_status,
            forged,
            next_status=PrincipalStatus.DISABLED,
        )

    def test_identity_subclass_rejected(self) -> None:
        class ForgedPrincipal(PrincipalIdentity):
            __slots__ = ()

        forged = object.__new__(ForgedPrincipal)
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT,
            transition_principal_status,
            forged,
            next_status=PrincipalStatus.DISABLED,
        )


class AliasDeclarationTests(unittest.TestCase):
    def test_valid_username_alias(self) -> None:
        declaration = _alias(alias="alice")
        self.assertEqual(declaration.alias, "alice")
        self.assertIs(declaration.alias_type, PrincipalAliasType.USERNAME)

    def test_valid_bearer_owner_alias(self) -> None:
        declaration = _alias(
            alias="service-owner",
            alias_type=PrincipalAliasType.BEARER_OWNER,
        )
        self.assertIs(declaration.alias_type, PrincipalAliasType.BEARER_OWNER)

    def test_alias_lowercase_normalization(self) -> None:
        self.assertEqual(_alias(alias="ALIce").alias, "alice")

    def test_alias_trimming(self) -> None:
        self.assertEqual(_alias(alias="  alice\t").alias, "alice")

    def test_unicode_alias_compatibility(self) -> None:
        self.assertEqual(_alias(alias="  Élodie  ").alias, "élodie")

    def test_unicode_is_not_silently_normalized(self) -> None:
        composed = _alias(alias="é")
        decomposed = _alias(alias="e\u0301")
        self.assertNotEqual(composed.alias, decomposed.alias)

    def test_empty_normalized_alias_rejected(self) -> None:
        for value in ("", "   ", "\t\n"):
            with self.subTest(value=repr(value)):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS,
                    _alias,
                    alias=value,
                )

    def test_control_character_alias_rejected(self) -> None:
        for value in ("ali\nce", "ali\tce", "ali\u200bce"):
            with self.subTest(value=repr(value)):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS,
                    _alias,
                    alias=value,
                )

    def test_nul_alias_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS,
            _alias,
            alias="ali\x00ce",
        )

    def test_exact_utf8_alias_bound_accepted(self) -> None:
        alias = "é" * (MAX_ALIAS_UTF8_BYTES // 2)
        self.assertEqual(len(_alias(alias=alias).alias.encode("utf-8")), MAX_ALIAS_UTF8_BYTES)

    def test_excessive_utf8_alias_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS,
            _alias,
            alias="é" * (MAX_ALIAS_UTF8_BYTES // 2 + 1),
        )

    def test_reserved_aliases_rejected(self) -> None:
        for value in ("internal-tool", "api", "anonymous", "demo", "system"):
            with self.subTest(value=value):
                _assert_code(
                    self,
                    PrincipalIdentityCode.RESERVED_ALIAS,
                    _alias,
                    alias=f" {value.upper()} ",
                )

    def test_alias_requires_exact_string(self) -> None:
        class HostileString(str):
            def strip(self, *args: object, **kwargs: object) -> str:
                raise AssertionError("must not run")

        for value in (None, 7, b"alice", HostileString("alice")):
            with self.subTest(value=type(value).__name__):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS,
                    _alias,
                    alias=value,
                )

    def test_invalid_alias_type_rejected(self) -> None:
        for value in ("username", "internal_tool", None):
            with self.subTest(value=value):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS_TYPE,
                    _alias,
                    alias_type=value,
                )

    def test_alias_declaration_exact_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(PrincipalAliasDeclaration)),
            ("principal_id", "alias", "alias_type"),
        )

    def test_alias_declaration_is_frozen_and_slotted(self) -> None:
        declaration = _alias()
        with self.assertRaises(FrozenInstanceError):
            declaration.alias = "changed"  # type: ignore[misc]
        self.assertFalse(hasattr(declaration, "__dict__"))

    def test_alias_repr_is_fixed_and_non_identifying(self) -> None:
        declaration = _alias(alias="privacy-canary-alias")
        rendered = repr(declaration)
        self.assertEqual(rendered, "PrincipalAliasDeclaration()")
        self.assertNotIn("privacy-canary-alias", rendered)
        self.assertNotIn(PRINCIPAL_A, rendered)
        self.assertNotIn("username", rendered)

    def test_username_rename_is_new_alias_same_principal(self) -> None:
        identity = _identity()
        before = _alias(identity=identity, alias="alice")
        after = _alias(identity=identity, alias="alice-renamed")
        self.assertEqual(before.principal_id, after.principal_id)
        self.assertNotEqual(before.alias, after.alias)

    def test_alias_snapshot_has_no_public_status_field(self) -> None:
        declaration = _alias(identity=_identity(status=PrincipalStatus.DISABLED))
        self.assertFalse(hasattr(declaration, "status"))

    def test_direct_alias_construction_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS_DECLARATION,
            PrincipalAliasDeclaration,
        )


class AliasResolutionTests(unittest.TestCase):
    def test_valid_unique_active_resolution(self) -> None:
        result = resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(_alias(alias="alice"),),
        )
        self.assertEqual(result.principal_id, PRINCIPAL_A)
        self.assertIs(result.status, PrincipalStatus.ACTIVE)

    def test_requested_alias_must_already_be_normalized(self) -> None:
        for value in ("Alice", " alice "):
            with self.subTest(value=value):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS,
                    resolve_principal_alias,
                    value,
                    alias_type=PrincipalAliasType.USERNAME,
                    declarations=(_alias(),),
                )

    def test_no_match_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_NOT_FOUND,
            resolve_principal_alias,
            "missing",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(_alias(alias="alice"),),
        )

    def test_same_alias_mapped_to_two_principals_rejected(self) -> None:
        declarations = (
            _alias(alias="alice"),
            _alias(identity=_identity(PRINCIPAL_B), alias="alice"),
        )
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )

    def test_duplicate_declaration_rejected(self) -> None:
        declaration = _alias()
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(declaration, declaration),
        )

    def test_disabled_principal_rejected(self) -> None:
        declaration = _alias(identity=_identity(status=PrincipalStatus.DISABLED))
        _assert_code(
            self,
            PrincipalIdentityCode.PRINCIPAL_DISABLED,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(declaration,),
        )

    def test_deleted_principal_rejected(self) -> None:
        declaration = _alias(identity=_identity(status=PrincipalStatus.DELETED))
        _assert_code(
            self,
            PrincipalIdentityCode.PRINCIPAL_DELETED,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(declaration,),
        )

    def test_alias_type_mismatch_rejected(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.BEARER_OWNER,
            declarations=(_alias(alias="alice"),),
        )

    def test_same_alias_under_both_types_for_same_principal_is_allowed(self) -> None:
        declarations = (
            _alias(alias="alice"),
            _alias(alias="alice", alias_type=PrincipalAliasType.BEARER_OWNER),
        )
        username = resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )
        bearer = resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.BEARER_OWNER,
            declarations=declarations,
        )
        self.assertEqual(username.principal_id, PRINCIPAL_A)
        self.assertEqual(bearer.principal_id, PRINCIPAL_A)

    def test_cross_type_alias_mapped_to_two_principals_rejected(self) -> None:
        declarations = (
            _alias(alias="alice"),
            _alias(
                identity=_identity(PRINCIPAL_B),
                alias="alice",
                alias_type=PrincipalAliasType.BEARER_OWNER,
            ),
        )
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )

    def test_active_and_disabled_snapshots_for_one_principal_conflict(self) -> None:
        declarations = (
            _alias(alias="alice-old"),
            _alias(
                identity=_identity(status=PrincipalStatus.DISABLED),
                alias="alice-new",
            ),
        )
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice-old",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )

    def test_active_and_deleted_snapshots_for_one_principal_conflict(self) -> None:
        declarations = (
            _alias(alias="alice-old"),
            _alias(
                identity=_identity(status=PrincipalStatus.DELETED),
                alias="alice-new",
            ),
        )
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_CONFLICT,
            resolve_principal_alias,
            "alice-old",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )

    def test_distinct_historical_aliases_for_same_principal_are_allowed(self) -> None:
        declarations = (_alias(alias="alice-old"), _alias(alias="alice-new"))
        result = resolve_principal_alias(
            "alice-new",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )
        self.assertEqual(result.principal_id, PRINCIPAL_A)

    def test_exact_builtin_tuple_required(self) -> None:
        declaration = _alias()
        for declarations in (
            [declaration],
            (item for item in (declaration,)),
        ):
            with self.subTest(kind=type(declarations).__name__):
                _assert_code(
                    self,
                    PrincipalIdentityCode.INVALID_ALIAS_SET,
                    resolve_principal_alias,
                    "alice",
                    alias_type=PrincipalAliasType.USERNAME,
                    declarations=declarations,
                )

    def test_hostile_iterable_is_not_iterated(self) -> None:
        class HostileIterable:
            def __iter__(self):
                raise AssertionError("must not iterate")

        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS_SET,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=HostileIterable(),
        )

    def test_excessive_tuple_rejected_before_item_validation(self) -> None:
        declarations = (object(),) * (MAX_ALIAS_DECLARATIONS + 1)
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS_SET,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )

    def test_empty_tuple_is_no_match(self) -> None:
        _assert_code(
            self,
            PrincipalIdentityCode.ALIAS_NOT_FOUND,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(),
        )

    def test_forged_alias_declaration_rejected(self) -> None:
        forged = object.__new__(PrincipalAliasDeclaration)
        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_ALIAS_DECLARATION,
            resolve_principal_alias,
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(forged,),
        )

    def test_exact_private_subtype_is_structural_not_authoritative(self) -> None:
        forged = object.__new__(principal_module._PrincipalAliasActive)
        object.__setattr__(forged, "principal_id", PRINCIPAL_A)
        object.__setattr__(forged, "alias", "alice")
        object.__setattr__(forged, "alias_type", PrincipalAliasType.USERNAME)
        result = resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(forged,),
        )
        self.assertEqual(result.principal_id, PRINCIPAL_A)
        for claim in ("authenticated", "authorized", "persisted", "current"):
            self.assertFalse(hasattr(result, claim))

    def test_input_tuple_unchanged(self) -> None:
        declaration = _alias()
        declarations = (declaration,)
        before = tuple(declarations)
        resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=declarations,
        )
        self.assertEqual(declarations, before)
        self.assertIs(declarations[0], declaration)

    def test_returned_identity_is_a_fresh_immutable_object(self) -> None:
        declaration = _alias()
        result = resolve_principal_alias(
            "alice",
            alias_type=PrincipalAliasType.USERNAME,
            declarations=(declaration,),
        )
        with self.assertRaises(FrozenInstanceError):
            result.status = PrincipalStatus.DISABLED  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))


class PrivacyAndClaimTests(unittest.TestCase):
    def test_errors_use_fixed_codes_and_fixed_repr(self) -> None:
        for code in PrincipalIdentityCode:
            with self.subTest(code=code):
                error = PrincipalIdentityError(code)
                self.assertEqual(str(error), code.value)
                self.assertEqual(
                    repr(error),
                    f"PrincipalIdentityError(code={code.value!r})",
                )

    def test_privacy_canaries_absent_from_error_and_streams(self) -> None:
        canary = "privacy-canary-user@example.invalid/home/secret"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            error = _assert_code(
                self,
                PrincipalIdentityCode.INVALID_ALIAS,
                _alias,
                alias=canary + "\x00",
            )
        combined = str(error) + repr(error) + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(canary, combined)

    def test_privacy_canary_absent_from_traceback(self) -> None:
        canary = "privacy-canary-token-value"
        try:
            _alias(alias=canary + "\x00")
        except PrincipalIdentityError as error:
            rendered = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        else:
            self.fail("expected a fixed contract error")
        self.assertNotIn(canary, rendered)

    def test_exception_chaining_is_suppressed(self) -> None:
        forged = object.__new__(PrincipalIdentity)
        try:
            _alias(identity=forged)
        except PrincipalIdentityError as error:
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
        else:
            self.fail("expected a fixed contract error")

    def test_hostile_values_are_not_rendered(self) -> None:
        class HostileValue:
            def __str__(self) -> str:
                raise AssertionError("must not stringify")

            def __repr__(self) -> str:
                raise AssertionError("must not repr")

        _assert_code(
            self,
            PrincipalIdentityCode.INVALID_PRINCIPAL_ID,
            _identity,
            HostileValue(),
        )

    def test_identity_makes_no_security_or_storage_claim(self) -> None:
        identity = _identity()
        for name in (
            "authenticated",
            "authorized",
            "account_exists",
            "persisted",
            "owner",
            "bearer_scope",
            "cookie_valid",
            "reservation",
        ):
            self.assertFalse(hasattr(identity, name))

    def test_alias_makes_no_uniqueness_or_authentication_claim(self) -> None:
        declaration = _alias()
        for name in (
            "authenticated",
            "authorized",
            "unique",
            "persisted",
            "current",
            "issuer",
        ):
            self.assertFalse(hasattr(declaration, name))


class StaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(PRODUCTION_PATH))

    def test_production_imports_are_standard_library_only(self) -> None:
        allowed = {"__future__", "dataclasses", "enum", "re", "typing", "unicodedata"}
        imports: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        self.assertTrue(imports <= allowed, imports - allowed)

    def test_no_prohibited_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "subprocess", "socket",
            "logging", "time", "datetime", "random", "secrets", "uuid",
            "asyncio", "app", "auth", "database", "sqlalchemy", "routes",
            "workers", "ffmpeg", "av", "faster_whisper", "numpy", "torch",
            "chromadb", "rag", "marketmatch_ingest_reservation",
            "marketmatch_media_attestation", "marketmatch_original_media_observation",
        }
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.lower().split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").lower().split(".")[0]}
            else:
                continue
            self.assertFalse(roots & prohibited, roots & prohibited)

    def test_no_io_logging_or_dynamic_execution_calls(self) -> None:
        prohibited_calls = {
            "open", "print", "input", "exec", "eval", "compile", "__import__",
        }
        called_names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(called_names & prohibited_calls)

    def test_no_time_random_identifier_or_async_constructs(self) -> None:
        prohibited_names = {
            "uuid4", "uuid1", "token_hex", "token_urlsafe", "randint", "random",
            "urandom", "now", "utcnow", "today", "time",
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(called_attributes & prohibited_names)
        self.assertFalse(
            any(isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom))
                for node in ast.walk(self.tree))
        )

    def test_no_sensitive_source_attributes_or_workflow_calls(self) -> None:
        prohibited_attributes = {
            "environ", "getenv", "read", "write", "connect", "execute", "commit",
            "authenticate", "authorize", "upload", "transcribe", "publish", "unlink",
            "remove", "mkdir", "makedirs", "open", "print", "log", "debug", "info",
        }
        attributes = {
            node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
        }
        self.assertFalse(attributes & prohibited_attributes)

    def test_public_dataclasses_have_exact_fields_and_fixed_repr(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(PrincipalIdentity)),
            ("principal_id", "status"),
        )
        self.assertEqual(
            tuple(item.name for item in fields(PrincipalAliasDeclaration)),
            ("principal_id", "alias", "alias_type"),
        )
        self.assertEqual(repr(_identity()), "PrincipalIdentity()")
        self.assertEqual(repr(_alias()), "PrincipalAliasDeclaration()")

    def test_no_global_mutable_state(self) -> None:
        for node in self.tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            self.assertNotIsInstance(value, (ast.List, ast.Dict, ast.Set))

    def test_public_functions_do_not_mutate_parameters(self) -> None:
        for function in (
            node for node in self.tree.body if isinstance(node, ast.FunctionDef)
        ):
            parameter_names = {
                argument.arg
                for argument in (
                    list(function.args.posonlyargs)
                    + list(function.args.args)
                    + list(function.args.kwonlyargs)
                )
            }
            for node in ast.walk(function):
                targets: list[ast.expr] = []
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    raw_targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    targets.extend(raw_targets)
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.assertNotIn(target.id, parameter_names)
                    elif isinstance(target, (ast.Attribute, ast.Subscript)):
                        root = target.value
                        while isinstance(root, (ast.Attribute, ast.Subscript)):
                            root = root.value
                        if isinstance(root, ast.Name):
                            self.assertNotIn(root.id, parameter_names)

    def test_no_identifier_generation_or_workflow_contract_imports(self) -> None:
        names = {node.id.lower() for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        self.assertFalse({"uuid", "secrets", "random", "operation_id", "media_id"} & names)
        self.assertNotIn("marketmatch_ingest_reservation", self.source)
        self.assertNotIn("marketmatch_media_attestation", self.source)
        self.assertNotIn("marketmatch_original_media_observation", self.source)

    def test_only_approved_worktree_paths_are_present(self) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {
            line[3:] for line in result.stdout.splitlines() if len(line) >= 4
        }
        self.assertTrue(changed <= APPROVED_PATHS, changed - APPROVED_PATHS)
        self.assertTrue(all((ROOT / path).is_file() for path in APPROVED_PATHS))


if __name__ == "__main__":
    unittest.main()
