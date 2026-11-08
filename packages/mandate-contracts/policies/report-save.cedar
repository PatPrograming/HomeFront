permit(principal, action == Mandate::Action::"report_save", resource)
  when { context.delegatorActive };
