# Azure Communication Services Email Quota Request

## Issue
New Azure Communication Services email subscriptions are blocked by default for sender reputation protection.

**Error:** `SubscriptionBlocked - All requests from this subscription are blocked due to sender reputation`

## Resolution Steps

### 1. Create Azure Support Ticket

Go to: [Azure Support Portal](https://azure.microsoft.com/support/create-ticket/)

### 2. Fill in the Following Details

**Issue Type:** Technical
**Service Type:** Azure Communication Services
**Problem Type:** Email → Rate limits and quotas
**Summary:** Request to unblock subscription for email sending

### 3. Include This Information in the Ticket

```
Customer Information
Company name: Rush University System for Health
Company website: https://www.rush.edu
Brief description: Healthcare policy retrieval system (RAG) that sends weekly evaluation reports

Email Service Information
Subscription ID: e5282183-61c9-4c17-a58a-9442db9594d5
Azure Communication Services Resource Name: rush-policy-comm
Email Service Name: rush-policy-email
Is your custom domain already set up: No, using Azure Managed Domain
Domain from which you are sending emails: 316c2bc4-97b2-4736-94ba-4b1e8a1d5e76.azurecomm.net

Usage Information
1. What type of emails do you send?
   Transactional - automated weekly evaluation reports for internal IT/AI team

2. Expected volume:
   - Maximum rate per minute: 1
   - Maximum rate per hour: 5
   - Maximum rate per day: 10
   Note: This is for internal weekly reports only, not bulk marketing

Additional Information
Source of email addresses: Internal Rush employee email addresses only (juan_rojas@rush.edu)
Unsubscribe management: Not applicable - internal system reports only
```

### 4. Expected Timeline

Azure typically responds within **1-2 business days**.

## Alternative: Custom Domain (Recommended for Production)

For production use, Microsoft recommends using a custom domain instead of the Azure-managed domain:

1. Register a subdomain (e.g., `notifications.rush.edu`)
2. Add DNS records for SPF, DKIM, DMARC
3. Configure as custom domain in Azure Email Communication Services

Benefits:
- Better sender reputation
- Higher sending limits
- Professional appearance (emails from @rush.edu domain)

## Resources

- [Azure Email Quota Increase Documentation](https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/email-quota-increase)
- [Sender Reputation Best Practices](https://learn.microsoft.com/en-us/azure/communication-services/concepts/email/sender-reputation-managed-suppression-list)
- [Service Limits](https://learn.microsoft.com/en-us/azure/communication-services/concepts/service-limits#email)
