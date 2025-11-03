# Simple Proxy

Most proxy services limit your concurrency to ~80 connections, and either charge you per-ip or per-traffic, both of which are wildly expensive for short-lived, bandwidth intensive jobs.

Simple proxy allows you to pay at-cost for datacenter proxies on your own cloud account.

## Pricing Example

**1TB download, 1 hour, 1000 IPs**

Pay-per-ip pricing: $750 ([source](https://oxylabs.io/products/datacenter-proxies))     
Pay-per-traffic pricing: $460 ([source](https://oxylabs.io/products/datacenter-proxies))    
Simple Proxy: **$15** ($0.015/hr x 1000, 3TB of bandwidth included for 1 hour)

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

# Notes

## Vultr VKE 

I use the nodetype `vc2-1c-2gb`, however there are a few node types cheaper than that, such as `vc2-1c-1gb`, or the ipv6 limited `vc2-1c-0.5gb-v6`. The problem is that these two node types are not supported in VKE (to my knowledge). Supporting them would require writing a provider that provisions the machines with the simpleproxy docker container (or just run it bare metal). This is doable, but requires extra effort. Feel free to open a PR for this and add cost savings of $0.008/hr/server (which works out to ~$8/hr if you are running 1000 machines)