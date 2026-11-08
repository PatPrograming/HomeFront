forbid(principal, action == Mandate::Action::"case_read", resource)
  unless { principal has role && ["chief_of_staff", "caseworker"].contains(principal.role) };
