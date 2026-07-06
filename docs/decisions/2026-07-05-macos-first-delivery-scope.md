# Decision: macOS-first delivery scope

Date: 2026-07-05
Status: active

## Decision

The active implementation, integration, acceptance, and release scope is macOS
only, using Python 3.11 or later.

Current development must prioritize functional completeness, project-wide
integration, data isolation, safety gates, deterministic behavior, and complete
macOS acceptance. Linux and Windows compatibility must not block current feature
delivery.

Linux and Windows adaptation is deferred until all planned functions are
implemented, integrated, and accepted on macOS. Cross-platform work will then be
planned as a separate follow-up phase with its own compatibility matrix, fixes,
and acceptance evidence.

This decision overrides any earlier plan or review assumption that treats Linux
or Windows compatibility as a current release gate. Historical test evidence on
other platforms remains valid historical evidence but does not define the active
scope.

## Current acceptance boundary

A current feature is eligible for completion only when:

1. Its macOS behavior satisfies the approved requirement and safety boundaries.
2. Relevant unit, integration, regression, security, and persistence tests pass
   on macOS.
3. Project-wide integration tests pass on macOS without Critical or Major
   findings.
4. The final diff contains only the intended task scope.
5. Any known Linux or Windows incompatibility is recorded as deferred rather
   than silently claimed as supported.

## Deferred platform phase

After macOS functional development and integrated acceptance are complete, a
separate cross-platform phase may cover:

- Linux and Windows path, filesystem, process, and temporary-directory behavior.
- Platform-specific SQLite, networking, permissions, and service management.
- Restoring a multi-platform CI matrix.
- Compatibility fixes that do not weaken macOS safety or behavior.
- Independent acceptance evidence for each supported platform.

## Consequences

- Current CI uses macOS as the required platform.
- Windows-only or Linux-only failures do not block current macOS delivery.
- New production code should avoid gratuitous platform coupling when that costs
  nothing, but no speculative compatibility abstraction is required.
- Documentation and completion reports must state that current support is macOS
  only until the deferred platform phase is completed.
