forbid(principal, action == Mandate::Action::"email_send_list", resource)
  unless { principal has role && ["chief_of_staff"].contains(principal.role) };
