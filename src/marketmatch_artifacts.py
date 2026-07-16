"""Confined path validation for future MarketMatch artifacts.

This module deliberately has no application dependencies and performs no
filesystem mutation.  Persisted artifact paths are untrusted.  Callers must
supply an explicit, domain-bound root and must not treat filename sanitization
as a security boundary.

The read resolver uses descriptor-relative, no-follow metadata checks when the
host can provide them.  It returns a validated path record, not an open file
descriptor.  Validation only describes the filesystem identities observed at
the end of the call: a returned path is not safe indefinitely.
The descriptor-safe read capability traverses from that explicit root and
yields a binary stream backed directly by the acquired final descriptor.  A
directory-entry replacement after acquisition does not redirect that open
descriptor.  The capability protects the opened file object, not a future
pathname lookup: callers must consume the yielded stream and must never close
it and reopen the persisted path.

The Phase 3J output constructor still creates nothing.  This module provides no
writable capability or publication operation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import io
import ntpath
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Iterator, Union


PathInput = Union[str, os.PathLike[str]]


class ArtifactDomain(str, Enum):
    """Trusted domain label bound to a caller-supplied artifact root."""

    CALLS = "calls"
    VIDEOS = "videos"


class ArtifactPathCode(str, Enum):
    """Fixed, privacy-preserving artifact validation result codes."""

    INVALID_ROOT = "INVALID_ROOT"
    ROOT_NOT_DIRECTORY = "ROOT_NOT_DIRECTORY"
    ROOT_SYMLINK = "ROOT_SYMLINK"
    ROOT_DOMAIN_MISMATCH = "ROOT_DOMAIN_MISMATCH"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    ABSOLUTE_PATH_NOT_ALLOWED = "ABSOLUTE_PATH_NOT_ALLOWED"
    PATH_ESCAPE = "PATH_ESCAPE"
    PATH_COMPONENT_SYMLINK = "PATH_COMPONENT_SYMLINK"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    ARTIFACT_NOT_REGULAR = "ARTIFACT_NOT_REGULAR"
    ARTIFACT_HARDLINKED = "ARTIFACT_HARDLINKED"
    DESTINATION_EXISTS = "DESTINATION_EXISTS"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_EXTENSION = "INVALID_EXTENSION"
    UNSUPPORTED_PLATFORM_GUARANTEE = "UNSUPPORTED_PLATFORM_GUARANTEE"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    OPEN_FAILED = "OPEN_FAILED"


class ArtifactPathError(Exception):
    """A fixed-code validation failure that never embeds rejected values."""

    def __init__(self, code: ArtifactPathCode):
        if not isinstance(code, ArtifactPathCode):
            code = ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class ArtifactRoot:
    """An explicit path bound to a trusted Calls or Videos domain label."""

    domain: ArtifactDomain
    path: PathInput

    def __repr__(self) -> str:
        domain = self.domain.value if isinstance(self.domain, ArtifactDomain) else "invalid"
        return f"ArtifactRoot(domain={domain!r}, path=<opaque>)"


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class ValidatedArtifactPath:
    """Opaque validation result with recorded root and artifact identities.

    ``path`` is the canonical path observed during validation.  It is not a
    capability and carries no guarantee against filesystem changes after the
    resolver returns.
    """

    _path: Path
    _allowed_root: ArtifactRoot
    _root_identity: _Identity
    _artifact_identity: _Identity

    @property
    def path(self) -> Path:
        return self._path

    @property
    def domain(self) -> ArtifactDomain:
        return self._allowed_root.domain

    def __fspath__(self) -> str:
        return os.fspath(self._path)

    def __repr__(self) -> str:
        return f"ValidatedArtifactPath(domain={self.domain.value!r}, path=<opaque>)"


@dataclass(slots=True)
class _RootState:
    binding: ArtifactRoot
    original_path: str
    canonical_path: str
    original_identity: _Identity
    canonical_identity: _Identity
    descriptor: int


@dataclass(frozen=True, slots=True)
class _ComponentRecord:
    parent_descriptor: int
    name: str
    identity: _Identity
    is_final: bool


_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")


def _fail(code: ArtifactPathCode) -> None:
    raise ArtifactPathError(code)


def _validation_checkpoint(stage: str) -> None:
    """Internal deterministic race-test seam; it has no runtime behavior."""

    del stage


def _io_checkpoint(stage: str) -> None:
    """Internal deterministic descriptor-race test seam; no runtime behavior."""

    del stage


def _validate_text(value: object, code: ArtifactPathCode) -> str:
    try:
        raw = os.fspath(value)  # type: ignore[arg-type]
    except Exception:
        _fail(code)
    if not isinstance(raw, str):
        _fail(code)
    if not raw or raw != raw.strip() or "\x00" in raw:
        _fail(code)
    if unicodedata.normalize("NFC", raw) != raw:
        _fail(code)
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in raw):
        _fail(code)
    return raw


def _validate_binding(binding: ArtifactRoot, expected_domain: ArtifactDomain) -> None:
    if not isinstance(binding, ArtifactRoot):
        _fail(ArtifactPathCode.INVALID_ROOT)
    if not isinstance(expected_domain, ArtifactDomain):
        _fail(ArtifactPathCode.ROOT_DOMAIN_MISMATCH)
    if not isinstance(binding.domain, ArtifactDomain) or binding.domain is not expected_domain:
        _fail(ArtifactPathCode.ROOT_DOMAIN_MISMATCH)


def _require_platform_guarantees() -> None:
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    supports_follow = getattr(os, "supports_follow_symlinks", set())
    required_constants = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_constants):
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)
    if os.open not in supports_dir_fd or os.stat not in supports_dir_fd:
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)
    if os.stat not in supports_follow:
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)


def _require_read_capability_guarantees() -> None:
    _require_platform_guarantees()
    if not hasattr(os, "O_NONBLOCK"):
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)


def _identity(metadata: os.stat_result) -> _Identity:
    device = getattr(metadata, "st_dev", None)
    inode = getattr(metadata, "st_ino", None)
    links = getattr(metadata, "st_nlink", None)
    if not isinstance(device, int) or not isinstance(inode, int) or not isinstance(links, int):
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)
    if device < 0 or inode <= 0 or links <= 0:
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)
    return _Identity(device=device, inode=inode)


def _same_identity(left: _Identity, right: _Identity) -> bool:
    return left.device == right.device and left.inode == right.inode


def _fstat_with_code(descriptor: int, code: ArtifactPathCode) -> os.stat_result:
    try:
        return os.fstat(descriptor)
    except OSError:
        _fail(code)


def _strict_realpath(path: str, code: ArtifactPathCode) -> str:
    try:
        return os.path.realpath(path, strict=True)
    except TypeError:
        _fail(ArtifactPathCode.UNSUPPORTED_PLATFORM_GUARANTEE)
    except (FileNotFoundError, NotADirectoryError):
        _fail(code)
    except OSError:
        _fail(code)


def _lstat_path(path: str, code: ArtifactPathCode) -> os.stat_result:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        _fail(code)
    except OSError:
        _fail(code)


def _open_root(binding: ArtifactRoot, expected_domain: ArtifactDomain) -> _RootState:
    _validate_binding(binding, expected_domain)
    _require_platform_guarantees()
    original_path = _validate_text(binding.path, ArtifactPathCode.INVALID_ROOT)
    if not os.path.isabs(original_path):
        _fail(ArtifactPathCode.INVALID_ROOT)
    if original_path.startswith(os.sep * 2):
        _fail(ArtifactPathCode.INVALID_ROOT)

    original_metadata = _lstat_path(original_path, ArtifactPathCode.INVALID_ROOT)
    if stat.S_ISLNK(original_metadata.st_mode):
        _fail(ArtifactPathCode.ROOT_SYMLINK)
    if not stat.S_ISDIR(original_metadata.st_mode):
        _fail(ArtifactPathCode.ROOT_NOT_DIRECTORY)
    original_identity = _identity(original_metadata)

    canonical_path = _strict_realpath(original_path, ArtifactPathCode.INVALID_ROOT)
    canonical_metadata = _lstat_path(canonical_path, ArtifactPathCode.INVALID_ROOT)
    if stat.S_ISLNK(canonical_metadata.st_mode):
        _fail(ArtifactPathCode.ROOT_SYMLINK)
    if not stat.S_ISDIR(canonical_metadata.st_mode):
        _fail(ArtifactPathCode.ROOT_NOT_DIRECTORY)
    canonical_identity = _identity(canonical_metadata)
    if not _same_identity(original_identity, canonical_identity):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(canonical_path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _fail(ArtifactPathCode.ROOT_SYMLINK)
        _fail(ArtifactPathCode.INVALID_ROOT)

    try:
        descriptor_metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(descriptor_metadata.st_mode):
            _fail(ArtifactPathCode.ROOT_NOT_DIRECTORY)
        if not _same_identity(canonical_identity, _identity(descriptor_metadata)):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        state = _RootState(
            binding=binding,
            original_path=original_path,
            canonical_path=canonical_path,
            original_identity=original_identity,
            canonical_identity=canonical_identity,
            descriptor=descriptor,
        )
        _validation_checkpoint("root_recorded")
        _revalidate_root(state)
        return state
    except BaseException:
        os.close(descriptor)
        raise


def _revalidate_root(state: _RootState) -> None:
    try:
        original_metadata = os.lstat(state.original_path)
        canonical_metadata = os.lstat(state.canonical_path)
        descriptor_metadata = os.fstat(state.descriptor)
        current_realpath = os.path.realpath(state.original_path, strict=True)
    except (OSError, TypeError):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)

    if stat.S_ISLNK(original_metadata.st_mode) or stat.S_ISLNK(canonical_metadata.st_mode):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    if not stat.S_ISDIR(original_metadata.st_mode) or not stat.S_ISDIR(canonical_metadata.st_mode):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    if not stat.S_ISDIR(descriptor_metadata.st_mode):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    if os.path.normcase(current_realpath) != os.path.normcase(state.canonical_path):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    identities = (
        (state.original_identity, _identity(original_metadata)),
        (state.canonical_identity, _identity(canonical_metadata)),
        (state.canonical_identity, _identity(descriptor_metadata)),
    )
    if any(not _same_identity(expected, observed) for expected, observed in identities):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)


def _contains_traversal_token(path: str) -> bool:
    return any(part == ".." for part in path.replace("\\", "/").split("/"))


def _relative_parts(path: str) -> tuple[str, ...]:
    if _contains_traversal_token(path):
        _fail(ArtifactPathCode.PATH_ESCAPE)
    portable_drive, _ = ntpath.splitdrive(path)
    if portable_drive:
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    if os.name == "nt":
        normalized_separators = path.replace("/", "\\")
    else:
        if "\\" in path:
            _fail(ArtifactPathCode.INVALID_CANDIDATE)
        normalized_separators = path
    raw_parts = normalized_separators.split(os.sep)
    if any(part in {"", "."} for part in raw_parts):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    normalized = os.path.normpath(normalized_separators)
    if normalized != normalized_separators or os.path.isabs(normalized):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    parts = tuple(Path(normalized).parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail(ArtifactPathCode.PATH_ESCAPE)
    return parts


def _absolute_relative_parts(state: _RootState, candidate: str) -> tuple[str, ...]:
    if not os.path.isabs(candidate):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    if candidate.startswith(os.sep * 2):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    if _contains_traversal_token(candidate):
        _fail(ArtifactPathCode.PATH_ESCAPE)
    normalized = os.path.normpath(candidate)
    if normalized != candidate:
        _fail(ArtifactPathCode.INVALID_CANDIDATE)

    for base in (state.original_path, state.canonical_path):
        try:
            common = os.path.commonpath((base, normalized))
        except (OSError, ValueError):
            continue
        if os.path.normcase(common) != os.path.normcase(base):
            continue
        relative = os.path.relpath(normalized, base)
        if relative == os.curdir:
            _fail(ArtifactPathCode.ARTIFACT_NOT_REGULAR)
        return _relative_parts(relative)
    _fail(ArtifactPathCode.PATH_ESCAPE)


def _stat_at(parent_descriptor: int, name: str, missing_code: ArtifactPathCode) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        _fail(missing_code)
    except OSError:
        _fail(missing_code)


def _open_directory_at(parent_descriptor: int, name: str, expected: _Identity) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if not _same_identity(expected, _identity(metadata)):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _revalidate_components(records: list[_ComponentRecord]) -> None:
    for record in records:
        try:
            metadata = os.stat(
                record.name,
                dir_fd=record.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if stat.S_ISLNK(metadata.st_mode):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if not _same_identity(record.identity, _identity(metadata)):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if record.is_final:
            if not stat.S_ISREG(metadata.st_mode):
                _fail(ArtifactPathCode.ARTIFACT_NOT_REGULAR)
            if metadata.st_nlink != 1:
                _fail(ArtifactPathCode.ARTIFACT_HARDLINKED)
        elif not stat.S_ISDIR(metadata.st_mode):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)


def _resolve_artifact(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
    allow_absolute: bool,
) -> ValidatedArtifactPath:
    state = _open_root(allowed_root, expected_domain)
    opened_descriptors: list[int] = [state.descriptor]
    records: list[_ComponentRecord] = []
    try:
        candidate_text = _validate_text(candidate, ArtifactPathCode.INVALID_CANDIDATE)
        host_absolute = os.path.isabs(candidate_text)
        portable_absolute = host_absolute or ntpath.isabs(candidate_text)
        if portable_absolute and not allow_absolute:
            _fail(ArtifactPathCode.ABSOLUTE_PATH_NOT_ALLOWED)
        if allow_absolute:
            if not host_absolute:
                _fail(ArtifactPathCode.INVALID_CANDIDATE)
            parts = _absolute_relative_parts(state, candidate_text)
        else:
            parts = _relative_parts(candidate_text)

        parent_descriptor = state.descriptor
        for component in parts[:-1]:
            metadata = _stat_at(
                parent_descriptor,
                component,
                ArtifactPathCode.ARTIFACT_NOT_FOUND,
            )
            if stat.S_ISLNK(metadata.st_mode):
                _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(ArtifactPathCode.ARTIFACT_NOT_FOUND)
            identity = _identity(metadata)
            records.append(
                _ComponentRecord(
                    parent_descriptor=parent_descriptor,
                    name=component,
                    identity=identity,
                    is_final=False,
                )
            )
            parent_descriptor = _open_directory_at(parent_descriptor, component, identity)
            opened_descriptors.append(parent_descriptor)

        final_name = parts[-1]
        final_metadata = _stat_at(
            parent_descriptor,
            final_name,
            ArtifactPathCode.ARTIFACT_NOT_FOUND,
        )
        if stat.S_ISLNK(final_metadata.st_mode):
            _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
        if not stat.S_ISREG(final_metadata.st_mode):
            _fail(ArtifactPathCode.ARTIFACT_NOT_REGULAR)
        if final_metadata.st_nlink != 1:
            _fail(ArtifactPathCode.ARTIFACT_HARDLINKED)
        final_identity = _identity(final_metadata)
        records.append(
            _ComponentRecord(
                parent_descriptor=parent_descriptor,
                name=final_name,
                identity=final_identity,
                is_final=True,
            )
        )

        target = os.path.join(state.canonical_path, *parts)
        canonical_target = _strict_realpath(target, ArtifactPathCode.ARTIFACT_NOT_FOUND)
        try:
            common = os.path.commonpath((state.canonical_path, canonical_target))
        except (OSError, ValueError):
            _fail(ArtifactPathCode.PATH_ESCAPE)
        if os.path.normcase(common) != os.path.normcase(state.canonical_path):
            _fail(ArtifactPathCode.PATH_ESCAPE)

        _validation_checkpoint("candidate_recorded")
        _revalidate_root(state)
        _revalidate_components(records)
        final_target = _strict_realpath(target, ArtifactPathCode.IDENTITY_CHANGED)
        if os.path.normcase(final_target) != os.path.normcase(canonical_target):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        _revalidate_root(state)
        _revalidate_components(records)
        return ValidatedArtifactPath(
            _path=Path(final_target),
            _allowed_root=allowed_root,
            _root_identity=state.canonical_identity,
            _artifact_identity=final_identity,
        )
    finally:
        for descriptor in reversed(opened_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def resolve_artifact_for_read(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
) -> ValidatedArtifactPath:
    """Validate an existing relative artifact beneath an explicit root.

    Absolute candidates are rejected.  No artifact content is opened or read.
    """

    return _resolve_artifact(
        allowed_root,
        candidate,
        expected_domain=expected_domain,
        allow_absolute=False,
    )


def resolve_absolute_artifact_for_read(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
) -> ValidatedArtifactPath:
    """Explicit compatibility resolver for confined absolute persisted paths."""

    return _resolve_artifact(
        allowed_root,
        candidate,
        expected_domain=expected_domain,
        allow_absolute=True,
    )


def revalidate_artifact_for_read(validated: ValidatedArtifactPath) -> ValidatedArtifactPath:
    """Revalidate a prior result and require both recorded identities to match."""

    if not isinstance(validated, ValidatedArtifactPath):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)
    current = resolve_absolute_artifact_for_read(
        validated._allowed_root,
        validated._path,
        expected_domain=validated.domain,
    )
    if not _same_identity(validated._root_identity, current._root_identity):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    if not _same_identity(validated._artifact_identity, current._artifact_identity):
        _fail(ArtifactPathCode.IDENTITY_CHANGED)
    return current


def _validate_identifier(identifier: object) -> str:
    text = _validate_text(identifier, ArtifactPathCode.INVALID_IDENTIFIER)
    if not text.isascii() or ".." in text or not _IDENTIFIER_RE.fullmatch(text):
        _fail(ArtifactPathCode.INVALID_IDENTIFIER)
    return text


def _validate_extension(extension: object) -> str:
    text = _validate_text(extension, ArtifactPathCode.INVALID_EXTENSION)
    if (
        not text.isascii()
        or ".." in text
        or "/" in text
        or "\\" in text
        or not _EXTENSION_RE.fullmatch(text)
    ):
        _fail(ArtifactPathCode.INVALID_EXTENSION)
    return text


def _destination_exists(root_descriptor: int, filename: str) -> bool:
    try:
        os.stat(filename, dir_fd=root_descriptor, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def construct_output_path(
    allowed_root: ArtifactRoot,
    identifier: str,
    extension: str,
    allowed_extensions: tuple[str, ...],
    *,
    expected_domain: ArtifactDomain,
) -> Path:
    """Construct, but never create, a confined single-component output path."""

    safe_identifier = _validate_identifier(identifier)
    safe_extension = _validate_extension(extension)
    if type(allowed_extensions) is not tuple:
        _fail(ArtifactPathCode.INVALID_EXTENSION)
    extension_allowlist = tuple(_validate_extension(item) for item in allowed_extensions)
    if not extension_allowlist or safe_extension not in extension_allowlist:
        _fail(ArtifactPathCode.INVALID_EXTENSION)

    state = _open_root(allowed_root, expected_domain)
    try:
        filename = safe_identifier + safe_extension
        if _destination_exists(state.descriptor, filename):
            _fail(ArtifactPathCode.DESTINATION_EXISTS)
        destination = os.path.join(state.canonical_path, filename)
        try:
            common = os.path.commonpath((state.canonical_path, destination))
        except (OSError, ValueError):
            _fail(ArtifactPathCode.PATH_ESCAPE)
        if os.path.normcase(common) != os.path.normcase(state.canonical_path):
            _fail(ArtifactPathCode.PATH_ESCAPE)

        _validation_checkpoint("destination_checked")
        _revalidate_root(state)
        if _destination_exists(state.descriptor, filename):
            _fail(ArtifactPathCode.DESTINATION_EXISTS)
        _revalidate_root(state)
        return Path(destination)
    finally:
        try:
            os.close(state.descriptor)
        except OSError:
            pass


def _candidate_parts_for_capability(
    state: _RootState,
    candidate: PathInput,
    *,
    allow_absolute: bool,
) -> tuple[str, ...]:
    candidate_text = _validate_text(candidate, ArtifactPathCode.INVALID_CANDIDATE)
    host_absolute = os.path.isabs(candidate_text)
    portable_absolute = host_absolute or ntpath.isabs(candidate_text)
    if portable_absolute and not allow_absolute:
        _fail(ArtifactPathCode.ABSOLUTE_PATH_NOT_ALLOWED)
    if allow_absolute:
        if not host_absolute:
            _fail(ArtifactPathCode.INVALID_CANDIDATE)
        return _absolute_relative_parts(state, candidate_text)
    return _relative_parts(candidate_text)


def _require_regular_single_link(
    metadata: os.stat_result,
    *,
    non_regular_code: ArtifactPathCode,
    hardlink_code: ArtifactPathCode,
) -> _Identity:
    if not stat.S_ISREG(metadata.st_mode):
        _fail(non_regular_code)
    if metadata.st_nlink != 1:
        _fail(hardlink_code)
    return _identity(metadata)


def _open_read_descriptor(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
    validated: ValidatedArtifactPath | None,
    allow_absolute: bool,
) -> int:
    _require_read_capability_guarantees()
    if validated is not None and not isinstance(validated, ValidatedArtifactPath):
        _fail(ArtifactPathCode.INVALID_CANDIDATE)

    state = _open_root(allowed_root, expected_domain)
    opened_descriptors: list[int] = [state.descriptor]
    records: list[_ComponentRecord] = []
    try:
        if validated is not None:
            if validated.domain is not expected_domain:
                _fail(ArtifactPathCode.ROOT_DOMAIN_MISMATCH)
            if not _same_identity(validated._root_identity, state.canonical_identity):
                _fail(ArtifactPathCode.IDENTITY_CHANGED)

        parts = _candidate_parts_for_capability(
            state,
            candidate,
            allow_absolute=allow_absolute,
        )
        _io_checkpoint("read_root_opened")
        _revalidate_root(state)

        parent_descriptor = state.descriptor
        for component in parts[:-1]:
            metadata = _stat_at(
                parent_descriptor,
                component,
                ArtifactPathCode.ARTIFACT_NOT_FOUND,
            )
            if stat.S_ISLNK(metadata.st_mode):
                _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(ArtifactPathCode.ARTIFACT_NOT_FOUND)
            identity = _identity(metadata)
            records.append(
                _ComponentRecord(
                    parent_descriptor=parent_descriptor,
                    name=component,
                    identity=identity,
                    is_final=False,
                )
            )
            parent_descriptor = _open_directory_at(parent_descriptor, component, identity)
            opened_descriptors.append(parent_descriptor)

        final_name = parts[-1]
        final_metadata = _stat_at(
            parent_descriptor,
            final_name,
            ArtifactPathCode.ARTIFACT_NOT_FOUND,
        )
        if stat.S_ISLNK(final_metadata.st_mode):
            _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
        final_identity = _require_regular_single_link(
            final_metadata,
            non_regular_code=ArtifactPathCode.ARTIFACT_NOT_REGULAR,
            hardlink_code=ArtifactPathCode.ARTIFACT_HARDLINKED,
        )
        if validated is not None and not _same_identity(
            validated._artifact_identity,
            final_identity,
        ):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        records.append(
            _ComponentRecord(
                parent_descriptor=parent_descriptor,
                name=final_name,
                identity=final_identity,
                is_final=True,
            )
        )

        _io_checkpoint("before_read_final_open")
        _revalidate_root(state)
        _revalidate_components(records)

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOCTTY"):
            flags |= os.O_NOCTTY
        try:
            final_descriptor = os.open(
                final_name,
                flags,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                _fail(ArtifactPathCode.PATH_COMPONENT_SYMLINK)
            _fail(ArtifactPathCode.OPEN_FAILED)
        opened_descriptors.append(final_descriptor)

        opened_metadata = _fstat_with_code(final_descriptor, ArtifactPathCode.OPEN_FAILED)
        opened_identity = _require_regular_single_link(
            opened_metadata,
            non_regular_code=ArtifactPathCode.ARTIFACT_NOT_REGULAR,
            hardlink_code=ArtifactPathCode.ARTIFACT_HARDLINKED,
        )
        if not _same_identity(final_identity, opened_identity):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if validated is not None and not _same_identity(
            validated._artifact_identity,
            opened_identity,
        ):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)

        _io_checkpoint("read_descriptor_opened")
        post_checkpoint_identity = _require_regular_single_link(
            _fstat_with_code(final_descriptor, ArtifactPathCode.OPEN_FAILED),
            non_regular_code=ArtifactPathCode.ARTIFACT_NOT_REGULAR,
            hardlink_code=ArtifactPathCode.ARTIFACT_HARDLINKED,
        )
        if not _same_identity(opened_identity, post_checkpoint_identity):
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        if opened_descriptors[-1] != final_descriptor:
            _fail(ArtifactPathCode.IDENTITY_CHANGED)
        return opened_descriptors.pop()
    finally:
        for descriptor in reversed(opened_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def open_artifact_for_read(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
    validated: ValidatedArtifactPath | None = None,
) -> Iterator[io.FileIO]:
    """Yield a binary stream backed by one descriptor-relative read open.

    The final pathname is not reopened after its descriptor is acquired.
    Supplying a prior validation additionally binds the open descriptor to the
    root and artifact device/inode identities recorded by that validation.
    """

    descriptor = _open_read_descriptor(
        allowed_root,
        candidate,
        expected_domain=expected_domain,
        validated=validated,
        allow_absolute=False,
    )
    try:
        stream = io.FileIO(descriptor, mode="rb", closefd=True)
    except BaseException as original:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if not isinstance(original, Exception):
            raise
        _fail(ArtifactPathCode.OPEN_FAILED)
    try:
        yield stream
    finally:
        try:
            stream.close()
        except OSError:
            _fail(ArtifactPathCode.OPEN_FAILED)


@contextmanager
def open_absolute_artifact_for_read(
    allowed_root: ArtifactRoot,
    candidate: PathInput,
    *,
    expected_domain: ArtifactDomain,
    validated: ValidatedArtifactPath | None = None,
) -> Iterator[io.FileIO]:
    """Explicit absolute-path compatibility form of the read capability."""

    descriptor = _open_read_descriptor(
        allowed_root,
        candidate,
        expected_domain=expected_domain,
        validated=validated,
        allow_absolute=True,
    )
    try:
        stream = io.FileIO(descriptor, mode="rb", closefd=True)
    except BaseException as original:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if not isinstance(original, Exception):
            raise
        _fail(ArtifactPathCode.OPEN_FAILED)
    try:
        yield stream
    finally:
        try:
            stream.close()
        except OSError:
            _fail(ArtifactPathCode.OPEN_FAILED)
