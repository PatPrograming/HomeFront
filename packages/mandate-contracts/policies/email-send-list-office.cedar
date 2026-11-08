permit(principal, action == Mandate::Action::"email_send_list", resource)
  when { context.delegatorActive };
