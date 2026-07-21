# Deliberately no admin registration for this app (Charter Section 16.3,
# Admin immutability policy, option 1: excluded entirely). GatePolicyVersion
# and PackagePolicyAssignment are append/close-only historical models --
# registering them through the repository's usual blanket
# `admin.site.register` loop would grant any staff `change` permission a
# fully-writable default ModelAdmin, bypassing every lock, immutability
# guard, and audit event this app's service layer enforces. No Milestone 1
# Increment 1 requirement calls for a read-only ModelAdmin surface either,
# so this file intentionally registers nothing.
