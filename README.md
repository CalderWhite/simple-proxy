# Simple Proxy

[write blurb about how proxy services are wildly overpriced]

# Usage

Before you deploy your proxy servers, you must generate a password hash for them
to use for authentication. This is made simple with the given helper script in Python.

```bash
$ python3 gen_hash.py
```

Enter your desired password, and it will output the hash you need to use for deployment.

Here is an example command that will give you 5 proxy IPs backed by AWS Fargate

```bash
$ python3 simply_proxy_init.py run --count 5 --env PROXY_USER=default --env PROXY_PASSWORD_SHA256=<your hash> --region us-west-2
```

# Testing

There is a short test script to ensure your proxy is both working and sending
requests from a different ip than your own. You can run this test with:

```bash
$ pytest
```

Note that you must first set the value of your remote server in .test.env (see
the template provided in this repository).