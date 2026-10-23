/* Device-tree reference model only; runs the actual patched probe child block. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct device_node {
    const char *compatible;
    int available, refs;
    struct device_node *child, *sibling;
};
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct dwc3_msm { struct platform_device *dwc3; };
static int populate_result, populate_calls, find_calls, find_missing;
static struct device_node *selected;
static struct platform_device controller;

static void of_node_put(struct device_node *node)
{
    if (node) { assert(node->refs > 1); --node->refs; }
}

/* Pinned drivers/of/base.c: skip disabled nodes, get next and put previous. */
static struct device_node *of_get_next_available_child(
    const struct device_node *node, struct device_node *prev)
{
    struct device_node *next;
    if (!node) return NULL;
    next = prev ? prev->sibling : node->child;
    for (; next; next = next->sibling) {
        if (!next->available) continue;
        ++next->refs;
        break;
    }
    of_node_put(prev);
    return next;
}

#define for_each_available_child_of_node(parent, child) \
    for (child = of_get_next_available_child(parent, NULL); child != NULL; \
         child = of_get_next_available_child(parent, child))

static int of_device_is_compatible(struct device_node *node, const char *value)
{
    assert(node->refs == 2);
    return strcmp(node->compatible, value) == 0;
}

static int of_platform_populate(struct device_node *node, void *matches,
                                void *lookup, struct device *parent)
{
    assert(node == parent->of_node && !matches && !lookup);
    ++populate_calls;
    return populate_result;
}

static struct platform_device *of_find_device_by_node(struct device_node *node)
{
    assert(node->refs == 2); /* The selected node must stay held during lookup. */
    ++find_calls;
    selected = node;
    return find_missing ? NULL : &controller;
}

#define dev_err(...) ((void)0)
static int probe_child(struct platform_device *pdev)
{
    struct device_node *node = pdev->dev.of_node, *dwc3_node;
    struct dwc3_msm state = {0}, *mdwc = &state;
    int ret = 0;
/* DRIVER */
    return ret;
put_dwc3:
err:
    return ret;
}

int main(int argc, char **argv)
{
    struct device_node nodes[4] = {
        {"snps,dwc3", 1, 1, NULL, NULL},
        {"qcom,usbbam", 1, 1, NULL, NULL},
        {"snps,dwc3", 0, 1, NULL, NULL},
        {"vendor,unrelated", 1, 1, NULL, NULL}
    };
    struct device_node parent = {"qcom,dwc3", 1, 1, &nodes[0], NULL};
    struct platform_device pdev = {{&parent}};
    struct device_node *want = &nodes[0];
    int result, expected = 0, expected_populate = 1, expected_find = 1;
    assert(argc == 2);
    if (!strcmp(argv[1], "unrelated-first")) {
        parent.child = &nodes[1]; nodes[1].sibling = &nodes[0];
    } else if (!strcmp(argv[1], "disabled-first")) {
        parent.child = &nodes[2]; nodes[2].sibling = &nodes[0];
    } else if (!strcmp(argv[1], "no-match")) {
        parent.child = &nodes[1]; nodes[1].sibling = &nodes[3];
        nodes[3].sibling = &nodes[2];
        want = NULL; expected = -ENODEV; expected_populate = expected_find = 0;
    } else if (!strcmp(argv[1], "empty")) {
        parent.child = NULL;
        want = NULL; expected = -ENODEV; expected_populate = expected_find = 0;
    } else if (!strcmp(argv[1], "populate-failure")) {
        parent.child = &nodes[1]; nodes[1].sibling = &nodes[0];
        populate_result = -517; expected = -517; expected_find = 0; want = NULL;
    } else if (!strcmp(argv[1], "lookup-missing")) {
        find_missing = 1; /* Preserve the existing probe's return value. */
    } else {
        assert(!strcmp(argv[1], "controller-first"));
        nodes[0].sibling = &nodes[1];
    }
    result = probe_child(&pdev);
    if (result != expected || selected != want || populate_calls != expected_populate ||
        find_calls != expected_find) {
        fprintf(stderr, "%s: ret=%d expected=%d selected=%s populate=%d find=%d\n",
                argv[1], result, expected, selected ? selected->compatible : "none",
                populate_calls, find_calls);
        return 1;
    }
    assert(parent.refs == 1);
    for (unsigned int i = 0; i < 4; ++i) assert(nodes[i].refs == 1);
    puts("DWC3 child selection and node lifetime verified");
    return 0;
}
