import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { ContactResolver } from "../src/contacts/resolver.js";

describe("Contact Resolver", () => {
  test("resolves 10-digit Indian numbers with default country code 91", () => {
    const resolver = new ContactResolver({ defaultCountryCode: "91" });
    const resolved = resolver.resolveTarget("9876543210");
    assert.equal(resolved.phoneNumber, "919876543210");
    assert.equal(resolved.formatted, "+919876543210");
  });

  test("resolves with custom country code e.g. 1 (US)", () => {
    const resolver = new ContactResolver({ defaultCountryCode: "1" });
    const resolved = resolver.resolveTarget("9876543210");
    assert.equal(resolved.phoneNumber, "19876543210");
  });

  test("preserves already prefixed E.164 numbers", () => {
    const resolver = new ContactResolver();
    const resolved = resolver.resolveTarget("+91 70886 69912");
    assert.equal(resolved.phoneNumber, "917088669912");
  });

  test("registers and resolves in-memory contacts", () => {
    const resolver = new ContactResolver();
    resolver.registerContact("Rahul", "9876543210");
    const resolved = resolver.resolveTarget("Rahul");
    assert.equal(resolved.name, "Rahul");
    assert.equal(resolved.phoneNumber, "9876543210");
  });

  test("rejects invalid non-phone strings", () => {
    const resolver = new ContactResolver();
    assert.throws(() => {
      resolver.resolveTarget("UnknownPerson");
    }, /neither a recognized contact name nor a valid phone number/);
  });
});
