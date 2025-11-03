# Simple Proxy

Most proxy services limit your concurrency to ~80 connections, and either charge you per-ip or per-traffic, both of which are wildly expensive for short-lived, bandwidth intensive jobs.

Simple proxy allows you to pay at-cost for datacenter proxies on your own cloud account.

## Pricing Example

**1TB download, 1 hour, 1000 IPs**

Pay-per-ip pricing: $750 ([source](https://oxylabs.io/products/datacenter-proxies))     
Pay-per-traffic pricing: $460 ([source](https://oxylabs.io/products/datacenter-proxies))    
Simple Proxy: **$20**    

I'm eyeballing the $20 calculation, but the idea is you are only paying for the ipv4 addresses, fargate containers (lowest priced containers), and the egress ($0.08/GB).

The advantage here is that you wanted to fan out massively for a short period of time and suck down a ton of data. Off-the-shelf proxy solutions simply don't accomodate this.


# Usage

```bash
$ python3 -m cwhite-simple-proxy --count 3 --region us-east-1 --provider aws_fargate --username default --password MY_PASSWORD
```

After you're done,

```bash
$ python3 -m cwhite-simple-proxy cleanup --provider aws_fargate --region us-east-1
```


# Testing

There is a short test script to ensure your proxy is both working and sending
requests from a different ip than your own. You can run this test with:

```bash
$ pytest
```

Note that you must first set the value of your remote server in .test.env (see
the template provided in this repository).