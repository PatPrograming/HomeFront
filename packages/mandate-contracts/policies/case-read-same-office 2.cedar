permit(principal, action == Mandate::Action::"case_read", resource)
  when { context.delegatorActive && resource.office == principal.office };
