permit(principal, action == Mandate::Action::"email_send", resource)
  when { context.delegatorActive && context.recipientKnown };
