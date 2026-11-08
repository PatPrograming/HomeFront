forbid(principal, action == Mandate::Action::"email_send_list", resource)
  unless { principal has roleTools && principal.roleTools.contains("email_send_list") && principal has role && ["chief_of_staff"].contains(principal.role) };
