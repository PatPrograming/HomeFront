forbid(principal, action == Mandate::Action::"email_send", resource)
  unless { principal has role && ["chief_of_staff", "caseworker", "communications_director"].contains(principal.role) };
